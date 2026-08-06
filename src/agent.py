"""Agent loop — src/contracts.py:agent_turn (issues #7, #11, #12, #13, #14, #15).

Pipeline: degraded-mode check -> guardrail -> session replay -> Sonnet tool
loop with exactly the three tools (CLAUDE.md rule 4) -> grounded, streamed
answer. If the snapshot is missing and Snowflake is unreachable (PRD §4.1),
the turn short-circuits with an honest message before even the guardrail
runs — Sonnet and Snowflake are never touched. A REFUSE verdict
short-circuits before the tool loop similarly — Sonnet and Snowflake are
never touched for a refused turn. Any `run_census_sql` failure (gate
rejection or a genuine execution error) or a zero-row result counts against
a per-turn recovery budget (MAX_RECOVERY_RETRIES, CLAUDE.md rule 9); once
spent, the loop stops asking the model and emits a deterministic
honest-failure message naming what was tried — code-enforced termination,
not a request the model can decline. An unresolved ambiguous
`resolve_geography` result gets the same code-enforced treatment
(CLAUDE.md rule 10): if the model tries `run_census_sql` anyway instead of
asking, the turn is force-terminated with a deterministic clarifying
question before Snowflake is ever touched. `TURN_DEADLINE_S` is a soft
wall-clock watchdog checked once per round boundary (never mid-call); once
crossed, no further model or tool calls are issued and the turn ends with a
deterministic partial answer built only from rows this turn's queries
actually returned (rule 2's grounding guarantee still holds even when time
runs out) — the stream still always ends in DONE.

No agent framework (CLAUDE.md rule 14) — a hand-written loop over the
Anthropic SDK's async streaming client, so tool_start/tool_end/token events
can be emitted as they happen (rule 11) rather than after the fact.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from src import tools as census_tools
from src.contracts import (
    MAX_RECOVERY_RETRIES,
    TURN_DEADLINE_S,
    ChatEvent,
    ChatMessage,
    EventType,
    GeoLevel,
    GuardrailAction,
    RefusalCategory,
    SqlRejected,
)
from src.guardrail import classify_input
from src.health import is_degraded
from src.model_config import AGENT_MODEL
from src.sessions import append_message, get_session

logger = logging.getLogger(__name__)

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

# Issue #15 / PRD §4.1, §5: an honest message rather than crashing, hanging,
# or fabricating an answer when the snapshot is missing and Snowflake is
# unreachable — the app literally has no way to search variables, resolve
# geography, or run a query.
_DEGRADED_MESSAGE = (
    "I'm having trouble connecting to the census data right now, so I "
    "can't answer this. Please try again in a few minutes."
)

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
1. Every variable_id column reference MUST be double-quoted, exactly as returned by search_census_variables (e.g. "B01001e23", not B01001e23). These columns were created case-sensitively; an unquoted reference gets folded to uppercase by Snowflake and fails with "invalid identifier" even though the column exists. This is the single most common cause of a failed query — quote every variable_id, every time, with no exceptions.
2. Counts roll up by SUM. Medians never do — a variable_id's geo_levels field tells you this (median variables report only block_group as valid; count variables report all five levels). Never average or SUM a median across CBGs.
3. A true mean IS computable where a numerator/denominator pair of aggregate variables exists: SUM(numerator)/SUM(denominator) at any level. If asked for an "average" above block-group level, use this and state the substitution explicitly.
4. SQL NULL in a demographic column means "not reported," never 0 — never coerce it, never SUM it as zero.
5. Check each variable's universe (population vs. households vs. workers, etc.) before dividing one by another — they are not interchangeable denominators.
6. Every query names its columns explicitly and either aggregates to the asked-for level or carries its own ORDER BY ... LIMIT sized to the question (1 row for a single place, a handful for a comparison). Never SELECT *.

Aggregation pattern (SUM over the CBGs in the requested geography, using a variable_id you got from search_census_variables — replace <variable_id> and <table> with real values from your tool results, keeping the double quotes around <variable_id> exactly as shown):
  SELECT SUM("<variable_id>") FROM US_CENSUS.PUBLIC."<table>" WHERE SUBSTR(CENSUS_BLOCK_GROUP,1,5) = '<county_geo_id>'

Grounding — the single most important rule: every number in your answer must come from this turn's run_census_sql results. If a query returns zero rows, that is an honest "not found" — never state a number that didn't come back from a query. If something fails, say plainly what you tried and what happened; do not fabricate a plausible-sounding answer.

This is a 5-year rolling estimate, never a point-in-time count — phrase answers accordingly ("an estimated X", not "there are exactly X").

Ambiguity and defaults:
- Every answer assumes 2020 ACS 5-year estimates (2016-2020) unless the user says otherwise. This is a default you apply, not a silent one — state it in your answer (e.g. "using 2020 ACS 5-year estimates...").
- If resolve_geography returns ambiguous=True, it found 2+ matching places. You MUST ask the user which one they mean, naming each candidate — never guess or silently pick one, even if one seems more likely.
- This data has no city or place boundaries, only state and county. If asked about a city (e.g. "Austin", "Springfield"), say plainly that no city boundary exists in this data, then offer the containing county by name as an explicit substitute — ask the user to confirm before using it. Never silently answer with the county's number as if it were the city's; a city is usually a fraction of its county's population.
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


def _build_recovery_failure_message(recovery_log: list[str]) -> str:
    """Deterministic, code-generated honest failure (CLAUDE.md rule 9) —
    never LLM-generated, so exhaustion is a code-enforced stop rather than
    a request the model can decline. Lists exactly what was tried so the
    user (or an eval) can see it wasn't a silent empty response."""
    tried = "\n".join(f"- {entry}" for entry in recovery_log)
    return (
        f"I tried {len(recovery_log)} times to get a grounded answer to this "
        f"and couldn't:\n{tried}\n\nRather than guess, I'm stopping here — "
        "try rephrasing the question or naming the geography more specifically."
    )


_WATCHDOG_PARTIAL_ROW_CAP = 50


def _build_watchdog_partial_message(collected_query_results: list[dict[str, Any]]) -> str:
    """Deterministic, code-generated partial answer (CLAUDE.md rule 11,
    issue #14: TURN_DEADLINE_S watchdog). Never asks the model to summarize
    — that would spend more of the very budget that's already exhausted,
    and risks the model narrating a number that isn't actually grounded in
    a row from this turn. Uses only rows this turn's run_census_sql calls
    actually returned (rule 2) — never fabricates a number to fill the gap.
    Capped at _WATCHDOG_PARTIAL_ROW_CAP: successful run_census_sql calls
    aren't bounded by MAX_RECOVERY_RETRIES (only failures/zero-row results
    spend that budget), so up to _MAX_TOOL_LOOP_ITERATIONS rounds of
    full-width SQL_ROW_LIMIT results could otherwise accumulate into one
    unbounded client-facing message."""
    rows = [row for payload in collected_query_results for row in payload.get("rows", [])]
    if not rows:
        return (
            "I ran out of time before finding an answer to this. Try a "
            "narrower or more specific question."
        )
    shown = rows[:_WATCHDOG_PARTIAL_ROW_CAP]
    lines = "\n".join(
        "- " + ", ".join(f"{key}: {value}" for key, value in row.items()) for row in shown
    )
    truncation_note = (
        f"\n(showing the first {_WATCHDOG_PARTIAL_ROW_CAP} of {len(rows)} rows collected)"
        if len(rows) > _WATCHDOG_PARTIAL_ROW_CAP
        else ""
    )
    return (
        "I ran out of time before finishing, but here's what this turn's "
        f"queries found:\n{lines}{truncation_note}\n\nThis may be incomplete — "
        "try asking again or narrowing the question."
    )


def _build_ambiguous_geo_clarification(unresolved: list[dict[str, Any]]) -> str:
    """Deterministic, code-generated stop (CLAUDE.md rule 10 / issue #13:
    ambiguous geography MUST prompt a clarifying question, never a silent
    pick — a contract-level MUST, not a suggestion). Same
    code-enforced-termination pattern as _build_recovery_failure_message, so
    it can't be skipped by a model that tries to proceed to SQL anyway.
    Takes every unresolved ambiguity from this turn, not just one — a turn
    can accumulate more than one (e.g. a comparison question naming two
    ambiguous counties)."""
    sections = []
    for geo_resolution in unresolved:
        candidates = geo_resolution.get("candidates", [])
        names = "\n".join(f"- {c['name']}" for c in candidates)
        query = geo_resolution.get("query") or "that"
        sections.append(f'"{query}" matches more than one place:\n{names}')
    return "\n\n".join(sections) + "\n\nWhich one did you mean?"


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

    # Issue #15 / PRD §4.1: checked before the guardrail so a degraded turn
    # doesn't also spend a Haiku call it has no use for. is_degraded()
    # short-circuits its own live Snowflake probe whenever a snapshot
    # exists, so this costs nothing extra on a healthy turn.
    if await asyncio.to_thread(is_degraded):
        yield ChatEvent(type=EventType.TOKEN, data={"text": _DEGRADED_MESSAGE})
        await asyncio.to_thread(
            append_message, session_id, ChatMessage(role="assistant", content=_DEGRADED_MESSAGE)
        )
        yield ChatEvent(
            type=EventType.DONE,
            data={"elapsed_ms": int((time.monotonic() - turn_start) * 1000)},
        )
        return

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
    recovery_attempts = 0
    recovery_log: list[str] = []
    recovery_exhausted = False
    # A list, not a single overwritten variable: an unrelated resolve_geography
    # call elsewhere in the turn must never clear a still-unresolved
    # ambiguity (code review of issue #13 caught the single-variable version
    # doing exactly that — an unambiguous result for place B silently
    # cleared an unresolved ambiguous result for place A, letting a later
    # run_census_sql through unblocked). Only appended to, never cleared,
    # for the rest of the turn.
    unresolved_ambiguous_geo: list[dict[str, Any]] = []
    ambiguity_blocked = False
    collected_query_results: list[dict[str, Any]] = []
    watchdog_fired = False

    for _ in range(_MAX_TOOL_LOOP_ITERATIONS):
        # Watchdog (CLAUDE.md rule 11, issue #14): TURN_DEADLINE_S is a
        # wall-clock budget checked once per round boundary, never mid-call
        # — a call already in flight always finishes and its tool_results
        # get appended normally; this only stops the *next* round from
        # starting. A soft budget under the assignment's 60s cap, not a
        # hard kill: the stream always still ends in DONE.
        if time.monotonic() - turn_start >= TURN_DEADLINE_S:
            watchdog_fired = True
            break

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

            # Ambiguity policy (CLAUDE.md rule 10 / issue #13): an unresolved
            # ambiguous resolve_geography result MUST produce a clarifying
            # question, never a silent pick — a contract-level MUST per
            # GeoResolution.ambiguous's own docstring. Trusting the model's
            # own judgment via the system prompt alone mirrors the SQL-gate
            # lesson (rule 5/D-013): a soft instruction is not a boundary.
            # If the model tries run_census_sql anyway while an ambiguity is
            # still unresolved, Snowflake is never touched — the turn is
            # force-terminated with a deterministic clarifying question
            # instead. Scoped to run_census_sql only, matching the issue's
            # literal exit criterion ("...instead of proceeding to SQL");
            # an unrelated *unambiguous* resolve_geography elsewhere in the
            # turn has no effect on this check (it isn't appended to
            # unresolved_ambiguous_geo). An unrelated *ambiguous* one is
            # tracked alongside the first — both must be resolved before
            # run_census_sql may proceed.
            if block.name == "run_census_sql" and unresolved_ambiguous_geo:
                yield ChatEvent(
                    type=EventType.TOOL_START,
                    data={"tool": block.name, "args_preview": _preview(block.input)},
                )
                yield ChatEvent(
                    type=EventType.TOOL_END,
                    data={"tool": block.name, "ok": False, "elapsed_ms": 0},
                )
                ambiguity_blocked = True
                break

            yield ChatEvent(
                type=EventType.TOOL_START,
                data={"tool": block.name, "args_preview": _preview(block.input)},
            )
            tool_start = time.monotonic()
            is_error = False
            # Two different error text tracks, deliberately kept separate:
            # `result_payload["error"]` goes back to the model as tool_result
            # content (private model context — fine to include real detail so
            # the model can self-correct, e.g. an unquoted-identifier hint).
            # `recovery_detail` is what code-generated recovery_log entries
            # use, which _build_recovery_failure_message streams to the
            # client and persists verbatim (src/app.py:28-30's "user-safe
            # text only" convention). A SqlRejected message is our own gate's
            # controlled vocabulary (validate_sql violations) — safe either
            # way. A bare `Exception` is a real driver/Snowflake error and
            # may carry internal detail, so it's sanitized before it can
            # reach recovery_detail; the raw exception is only logged
            # server-side.
            try:
                result_payload = await asyncio.to_thread(
                    _run_tool, block.name, block.input
                )
            except SqlRejected as exc:
                is_error = True
                result_payload = {"error": str(exc)}
                recovery_detail = str(exc)
            except Exception as exc:  # noqa: BLE001 — surfaced to the model as a tool error, not raised
                is_error = True
                result_payload = {"error": f"{block.name} failed: {exc}"}
                recovery_detail = f"{block.name} raised an internal error"
                logger.warning("Tool %s raised an unexpected exception", block.name, exc_info=exc)
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

            # Bounded recovery (CLAUDE.md rule 9): any run_census_sql failure
            # (gate rejection or a genuine Snowflake execution error — e.g. a
            # bad column reference inside an allowlisted table) or a
            # zero-row result spends the turn's recovery budget. Widened
            # from SqlRejected-only after a live run showed a real execution
            # error going uncounted, leaving the loop free to keep retrying
            # against Snowflake at full latency/cost — the exact thing rule
            # 9 bounds (D-013). Other tools don't count.
            if block.name == "run_census_sql":
                if is_error:
                    recovery_attempts += 1
                    recovery_log.append(f"run_census_sql failed: {recovery_detail}")
                elif result_payload.get("row_count") == 0:
                    recovery_attempts += 1
                    recovery_log.append("run_census_sql returned zero rows")
                else:
                    # Tracked for the watchdog (issue #14): if TURN_DEADLINE_S
                    # fires before a final answer, the partial message can
                    # only use rows a query in this turn actually returned
                    # (CLAUDE.md rule 2) — never a fabricated number.
                    collected_query_results.append(result_payload)
            elif block.name == "resolve_geography" and not is_error and result_payload.get("ambiguous"):
                unresolved_ambiguous_geo.append(result_payload)

        if ambiguity_blocked:
            break

        messages.append({"role": "user", "content": tool_results})

        # Checked once per model response, not per tool_use block: the
        # Anthropic API requires every tool_use in a response to get a
        # matching tool_result before the next request, so a batch
        # containing more than one failing run_census_sql call can push
        # recovery_attempts past MAX_RECOVERY_RETRIES by the size of that
        # batch before this check runs. Accepted as an unavoidable
        # consequence of that API contract, not a bug — MAX_RECOVERY_RETRIES
        # bounds attempts, it doesn't guarantee an exact cutoff mid-batch.
        if recovery_attempts >= MAX_RECOVERY_RETRIES:
            recovery_exhausted = True
            break
    else:
        streamed_text_parts.append(
            "\n\n[Stopped after reaching this turn's tool-call limit.]"
        )
        yield ChatEvent(
            type=EventType.TOKEN,
            data={"text": streamed_text_parts[-1]},
        )

    # Mutually exclusive by construction, not just by convention: ambiguity_blocked
    # and recovery_exhausted are only ever set after a round's tool
    # processing, and both immediately break the loop — so neither can
    # co-occur with watchdog_fired, which is only ever set at the very top
    # of an iteration, before that round's tool processing runs. The
    # if/elif order below is therefore never actually a tie-break between
    # them; it only matters as a readable priority list.
    if recovery_exhausted or ambiguity_blocked or watchdog_fired:
        if ambiguity_blocked:
            failure_text = _build_ambiguous_geo_clarification(unresolved_ambiguous_geo)
        elif watchdog_fired:
            failure_text = _build_watchdog_partial_message(collected_query_results)
        else:
            failure_text = _build_recovery_failure_message(recovery_log)
        yield ChatEvent(type=EventType.TOKEN, data={"text": failure_text})
        await asyncio.to_thread(
            append_message, session_id, ChatMessage(role="assistant", content=failure_text)
        )
        yield ChatEvent(
            type=EventType.DONE,
            data={"elapsed_ms": int((time.monotonic() - turn_start) * 1000)},
        )
        return

    final_answer = "".join(streamed_text_parts).strip()
    if final_answer:
        await asyncio.to_thread(
            append_message, session_id, ChatMessage(role="assistant", content=final_answer)
        )

    yield ChatEvent(
        type=EventType.DONE,
        data={"elapsed_ms": int((time.monotonic() - turn_start) * 1000)},
    )
