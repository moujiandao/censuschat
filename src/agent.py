"""Agent loop — src/contracts.py:agent_turn (issues #7 and #11).

Pipeline: guardrail -> session replay -> Sonnet tool loop with exactly the
three tools (CLAUDE.md rule 4) -> grounded, streamed answer. A REFUSE
verdict short-circuits before the tool loop — Sonnet and Snowflake are
never touched for a refused turn. Still deliberately excludes the bounded
recovery loop (issue #12) and 50s watchdog (issue #14) — both M3; the tool
loop below is their eventual hookup point, nothing here needs restructuring
to add them.

No agent framework (CLAUDE.md rule 14) — a hand-written loop over the
Anthropic SDK's async streaming client, so tool_start/tool_end/token events
can be emitted as they happen (rule 11) rather than after the fact.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from src import tools as census_tools
from src.contracts import (
    ChatEvent,
    ChatMessage,
    EventType,
    GeoLevel,
    GuardrailAction,
    RefusalCategory,
    SqlRejected,
)
from src.guardrail import classify_input
from src.model_config import AGENT_MODEL
from src.sessions import append_message, get_session

_client = anthropic.AsyncAnthropic()

# Infra safety net against a runaway tool loop — distinct from
# MAX_RECOVERY_RETRIES (contracts.py), which is a bounded-*recovery*
# business rule for issue #12 (SQL error / zero-row retries). This cap
# bounds the whole loop regardless of cause.
_MAX_TOOL_LOOP_ITERATIONS = 8

_MAX_TOKENS = 4096

_REFUSAL_MESSAGES: dict[RefusalCategory | None, str] = {
    RefusalCategory.OFF_TOPIC: (
        "I can only help with questions about US Census demographic data "
        "(population, income, housing, and similar ACS statistics)."
    ),
    RefusalCategory.ADVERSARIAL: (
        "I can't do that. I can help with questions about US Census "
        "demographic data."
    ),
    RefusalCategory.INAPPROPRIATE: "I can't help with that.",
    None: "I can only help with questions about US Census demographic data.",
}

# Architecture §2/PRD §4.2: the join topology (CBG decomposition, roll-up
# recipes, table-number -> physical-table mapping) is closed and tiny, so it
# is prompt content. The variable vocabulary is open and huge, so it is data
# reached only via search_census_variables — this prompt names no variable
# ID or label (CLAUDE.md rule 3). "B19xxx" below is the PRD's own worked
# example of the *pattern*, not a field.
SYSTEM_PROMPT = """You are censuschat, an assistant that answers questions about US demographics using 2020 ACS 5-year data (2016-2020 estimates) from Census block groups (CBGs).

You have exactly three tools:
- search_census_variables(query, limit): find variable IDs by natural-language description. Never guess or state a variable ID that didn't come from this tool.
- resolve_geography(name, level_hint): find geography IDs (state/county) by name. If the result is ambiguous (multiple candidates), ask the user which one they mean — never silently pick one.
- run_census_sql(sql): the only way to query Snowflake. Every SELECT must be validated by the SQL gate before it runs.

Data model — every demographic table is at CBG grain; there are no separate tract/county/state tables. The 12-character CENSUS_BLOCK_GROUP code decomposes as:
  positions 1-2   state FIPS
  positions 3-5   county FIPS
  positions 6-11  tract
  position 12     block group
Roll-up recipes: state = SUBSTR(CENSUS_BLOCK_GROUP,1,2); county = SUBSTR(CENSUS_BLOCK_GROUP,1,5); tract = SUBSTR(CENSUS_BLOCK_GROUP,1,11).
The physical table for a variable is selected by its TABLE_NUMBER prefix, e.g. a TABLE_NUMBER like B19xxx lives in table "2020_CBG_B19"; TABLE_ID suffix e<n> is the estimate, m<n> is its margin of error.

Correctness rules, in order of how costly a violation is:
1. Counts roll up by SUM. Medians never do — a variable_id's geo_levels field tells you this (median variables report only block_group as valid; count variables report all five levels). Never average or SUM a median across CBGs.
2. A true mean IS computable where a numerator/denominator pair of aggregate variables exists: SUM(numerator)/SUM(denominator) at any level. If asked for an "average" above block-group level, use this and state the substitution explicitly.
3. SQL NULL in a demographic column means "not reported," never 0 — never coerce it, never SUM it as zero.
4. Check each variable's universe (population vs. households vs. workers, etc.) before dividing one by another — they are not interchangeable denominators.
5. Every query names its columns explicitly and either aggregates to the asked-for level or carries its own ORDER BY ... LIMIT sized to the question (1 row for a single place, a handful for a comparison). Never SELECT *.

Aggregation pattern (SUM over the CBGs in the requested geography, using a variable_id you got from search_census_variables — replace <variable_id> and <table> with real values from your tool results):
  SELECT SUM(<variable_id>) FROM US_CENSUS.PUBLIC."<table>" WHERE SUBSTR(CENSUS_BLOCK_GROUP,1,5) = '<county_geo_id>'

Grounding — the single most important rule: every number in your answer must come from this turn's run_census_sql results. If a query returns zero rows, that is an honest "not found" — never state a number that didn't come back from a query. If something fails, say plainly what you tried and what happened; do not fabricate a plausible-sounding answer.

This is a 5-year rolling estimate, never a point-in-time count — phrase answers accordingly ("an estimated X", not "there are exactly X").
"""

_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "search_census_variables",
        "description": (
            "FTS5 search over the local Census variable snapshot. Returns "
            "matching variable_ids with coverage metadata (geo_levels, "
            "years) — never touches Snowflake."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of the demographic concept, e.g. 'total population' or 'median household income'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits to return (default 10).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "resolve_geography",
        "description": (
            "Lookup over the local geography index (state/county only). "
            "Returns ambiguous=True with all matching candidates when a "
            "name matches more than one place — ask the user rather than "
            "guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Place name, e.g. 'Alameda County, California' or 'Washington County'.",
                },
                "level_hint": {
                    "type": "string",
                    "enum": [level.value for level in GeoLevel],
                    "description": "Narrow the search to one geography level when known.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "run_census_sql",
        "description": (
            "The ONLY way to query Snowflake. Runs a SELECT through the SQL "
            "gate (allowlisted tables, LIMIT injected, single statement) "
            "and returns the rows. Raises a gate rejection with the "
            "specific violation if the query isn't allowed — fix and retry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SELECT statement, columns named explicitly, no SELECT *.",
                },
            },
            "required": ["sql"],
        },
    },
]

_TOOL_FUNCS = {
    "search_census_variables": census_tools.search_census_variables,
    "resolve_geography": census_tools.resolve_geography,
    "run_census_sql": census_tools.run_census_sql,
}


def _preview(tool_input: dict[str, Any]) -> str:
    return json.dumps(tool_input)[:200]


def _run_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Runs synchronously — called via asyncio.to_thread so a blocking
    Snowflake call (run_census_sql) never blocks the event loop."""
    kwargs = dict(tool_input)
    if name == "resolve_geography" and kwargs.get("level_hint"):
        kwargs["level_hint"] = GeoLevel(kwargs["level_hint"])

    result = _TOOL_FUNCS[name](**kwargs)
    return result.model_dump(mode="json")


def _trace_turn_stub(session_id: str) -> None:
    """Wiring point for issue #18 (Langfuse). One trace per agent_turn
    invocation, session_id in metadata, spans for guardrail/tool/model
    calls — deliberately a no-op here; #18 owns the langfuse dependency,
    LANGFUSE_HOST config, and span instrumentation. Never allowed to raise
    or block the turn regardless of what #18 adds."""


async def agent_turn(
    session_id: str, user_message: str
) -> AsyncIterator[ChatEvent]:
    """Full pipeline for one user turn (minimal M2 scope — see module
    docstring for what's deferred to M3). Every numeric claim originates
    from this turn's run_census_sql results; the stream always terminates
    with DONE (src/app.py converts any raised exception here into ERROR)."""
    turn_start = time.monotonic()
    _trace_turn_stub(session_id)

    session = await asyncio.to_thread(get_session, session_id)
    recent_turns = session.messages[-2:]
    messages: list[dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in session.messages
    ]
    messages.append({"role": "user", "content": user_message})
    await asyncio.to_thread(
        append_message, session_id, ChatMessage(role="user", content=user_message)
    )

    verdict = await asyncio.to_thread(classify_input, user_message, recent_turns)
    if verdict.action == GuardrailAction.REFUSE:
        refusal = _REFUSAL_MESSAGES.get(verdict.category, _REFUSAL_MESSAGES[None])
        yield ChatEvent(type=EventType.TOKEN, data={"text": refusal})
        await asyncio.to_thread(
            append_message, session_id, ChatMessage(role="assistant", content=refusal)
        )
        yield ChatEvent(
            type=EventType.DONE,
            data={"elapsed_ms": int((time.monotonic() - turn_start) * 1000)},
        )
        return

    streamed_text_parts: list[str] = []

    for _ in range(_MAX_TOOL_LOOP_ITERATIONS):
        async with _client.messages.stream(
            model=AGENT_MODEL,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=_TOOL_DEFS,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                streamed_text_parts.append(text)
                yield ChatEvent(type=EventType.TOKEN, data={"text": text})
            response = await stream.get_final_message()

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results: list[dict[str, Any]] = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            yield ChatEvent(
                type=EventType.TOOL_START,
                data={"tool": block.name, "args_preview": _preview(block.input)},
            )
            tool_start = time.monotonic()
            is_error = False
            try:
                result_payload = await asyncio.to_thread(
                    _run_tool, block.name, block.input
                )
            except SqlRejected as exc:
                is_error = True
                result_payload = {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 — surfaced to the model as a tool error, not raised
                is_error = True
                result_payload = {"error": f"{block.name} failed: {exc}"}
            elapsed_ms = int((time.monotonic() - tool_start) * 1000)

            yield ChatEvent(
                type=EventType.TOOL_END,
                data={"tool": block.name, "ok": not is_error, "elapsed_ms": elapsed_ms},
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result_payload),
                    "is_error": is_error,
                }
            )

        messages.append({"role": "user", "content": tool_results})
    else:
        streamed_text_parts.append(
            "\n\n[Stopped after reaching this turn's tool-call limit.]"
        )
        yield ChatEvent(
            type=EventType.TOKEN,
            data={"text": streamed_text_parts[-1]},
        )

    final_answer = "".join(streamed_text_parts).strip()
    if final_answer:
        await asyncio.to_thread(
            append_message, session_id, ChatMessage(role="assistant", content=final_answer)
        )

    yield ChatEvent(
        type=EventType.DONE,
        data={"elapsed_ms": int((time.monotonic() - turn_start) * 1000)},
    )
