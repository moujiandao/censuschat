"""Tests for the agent loop — src/contracts.py:agent_turn (issues #7, #11, #12, #13, #14).

Exercises the real function body (test_app.py stubs agent_turn entirely, so
none of this is covered there). The Anthropic client is faked rather than
mocked-through so the loop's actual control flow — guardrail short-circuit,
tool round trips, event sequencing — runs for real; only network I/O is
replaced.

No pytest-asyncio in this project's dependencies, so async generators are
driven directly via asyncio.run rather than adding one for a single test
module.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.agent as agent
import src.sessions as sessions
from src.contracts import (
    MAX_RECOVERY_RETRIES,
    ChatMessage,
    EventType,
    GuardrailAction,
    GuardrailVerdict,
    RefusalCategory,
    SqlGateResult,
    SqlRejected,
    SqlViolation,
)


@pytest.fixture(autouse=True)
def isolated_session_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_DB_PATH", tmp_path / "sessions.sqlite3")


class _FakeStream:
    def __init__(self, chunks: list[str], final_message) -> None:
        self._chunks = chunks
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    @property
    def text_stream(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()

    async def get_final_message(self):
        return self._final_message


class _FakeStreamFactory:
    """Stands in for _client.messages.stream — returns queued fake streams
    in order and records the kwargs each call was made with."""

    def __init__(self, streams: list[_FakeStream]) -> None:
        self._streams = list(streams)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self._streams.pop(0)


def _install_fake_client(monkeypatch, streams: list[_FakeStream]) -> _FakeStreamFactory:
    factory = _FakeStreamFactory(streams)
    fake_client = SimpleNamespace(messages=SimpleNamespace(stream=factory))
    monkeypatch.setattr(agent, "_client", fake_client)
    return factory


def _collect(session_id: str, message: str) -> list:
    async def _run():
        return [event async for event in agent.agent_turn(session_id, message)]

    return asyncio.run(_run())


def test_refuse_verdict_short_circuits_before_tool_loop(monkeypatch):
    """CLAUDE.md invariant: Sonnet and Snowflake are never touched for a
    refused turn. Fails loudly (AssertionError) if the tool loop is entered
    at all, rather than merely asserting on the output."""

    def _stream_should_not_be_called(**kwargs):
        raise AssertionError("Sonnet must never be called on a REFUSE verdict")

    monkeypatch.setattr(
        agent, "_client", SimpleNamespace(messages=SimpleNamespace(stream=_stream_should_not_be_called))
    )
    monkeypatch.setattr(
        agent,
        "classify_input",
        lambda message, recent_turns: GuardrailVerdict(
            action=GuardrailAction.REFUSE,
            category=RefusalCategory.OFF_TOPIC,
            reason="weather is not census data",
            latency_ms=12,
        ),
    )

    events = _collect("s-refuse", "what's the weather like?")

    assert [e.type for e in events] == [EventType.TOKEN, EventType.DONE]
    assert events[0].data["text"] == agent._REFUSAL_MESSAGES[RefusalCategory.OFF_TOPIC]

    session = sessions.get_session("s-refuse")
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert session.messages[1].content == agent._REFUSAL_MESSAGES[RefusalCategory.OFF_TOPIC]


def test_refuse_verdict_with_unmapped_category_uses_default_message(monkeypatch):
    monkeypatch.setattr(
        agent,
        "classify_input",
        lambda message, recent_turns: GuardrailVerdict(
            action=GuardrailAction.REFUSE, category=None, reason=None, latency_ms=5
        ),
    )

    def _stream_should_not_be_called(**kwargs):
        raise AssertionError("Sonnet must never be called on a REFUSE verdict")

    monkeypatch.setattr(
        agent, "_client", SimpleNamespace(messages=SimpleNamespace(stream=_stream_should_not_be_called))
    )

    events = _collect("s-refuse-default", "anything")

    assert events[0].data["text"] == agent._REFUSAL_MESSAGES[None]


def test_degraded_mode_short_circuits_before_guardrail_and_tool_loop(monkeypatch):
    """issue #15 / PRD §4.1: when the snapshot is missing and Snowflake is
    unreachable, the turn must respond with an honest message instead of
    crashing, hanging, or reaching the guardrail/model/Snowflake at all."""
    monkeypatch.setattr(agent, "is_degraded", lambda: True)

    def _classify_should_not_be_called(message, recent_turns):
        raise AssertionError("the guardrail must never run on a degraded turn")

    monkeypatch.setattr(agent, "classify_input", _classify_should_not_be_called)

    def _stream_should_not_be_called(**kwargs):
        raise AssertionError("Sonnet must never be called on a degraded turn")

    monkeypatch.setattr(
        agent, "_client", SimpleNamespace(messages=SimpleNamespace(stream=_stream_should_not_be_called))
    )

    events = _collect("s-degraded", "population of Wyoming?")

    assert [e.type for e in events] == [EventType.TOKEN, EventType.DONE]
    assert events[0].data["text"] == agent._DEGRADED_MESSAGE

    session = sessions.get_session("s-degraded")
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert session.messages[1].content == agent._DEGRADED_MESSAGE


def test_not_degraded_reaches_guardrail_normally(monkeypatch):
    """Regression guard: is_degraded() == False must not affect the normal
    pipeline at all."""
    monkeypatch.setattr(agent, "is_degraded", lambda: False)
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    final_message = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(monkeypatch, [_FakeStream(["ok"], final_message)])

    events = _collect("s-not-degraded", "population of Wyoming?")

    assert len(factory.calls) == 1
    assert events[-2].data["text"] == "ok"


def test_allow_verdict_reaches_tool_loop_and_terminates(monkeypatch):
    monkeypatch.setattr(
        agent,
        "classify_input",
        lambda message, recent_turns: GuardrailVerdict(
            action=GuardrailAction.ALLOW, category=None, reason=None, latency_ms=8
        ),
    )
    final_message = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(monkeypatch, [_FakeStream(["Wyoming has "], final_message)])

    events = _collect("s-allow", "population of Wyoming?")

    assert len(factory.calls) == 1
    assert [e.type for e in events] == [EventType.TOKEN, EventType.DONE]
    assert events[0].data["text"] == "Wyoming has "

    session = sessions.get_session("s-allow")
    assert session.messages[1].content == "Wyoming has"  # persisted answer is .strip()'d


def test_allow_verdict_runs_tool_round_trip_before_final_answer(monkeypatch):
    monkeypatch.setattr(
        agent,
        "classify_input",
        lambda message, recent_turns: GuardrailVerdict(
            action=GuardrailAction.ALLOW, category=None, reason=None, latency_ms=8
        ),
    )
    monkeypatch.setattr(
        agent, "_run_tool", lambda name, tool_input: {"hits": [{"variable_id": "B01001e1"}]}
    )

    tool_block = SimpleNamespace(
        type="tool_use", name="search_census_variables", input={"query": "population"}, id="tool-1"
    )
    first_response = SimpleNamespace(stop_reason="tool_use", content=[tool_block])
    second_response = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(
        monkeypatch,
        [
            _FakeStream([], first_response),
            _FakeStream(["found it."], second_response),
        ],
    )

    events = _collect("s-tool", "total population?")

    assert len(factory.calls) == 2
    event_types = [e.type for e in events]
    assert event_types == [
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOKEN,
        EventType.DONE,
    ]
    assert events[0].data["tool"] == "search_census_variables"
    assert events[1].data["ok"] is True


def _allow_verdict(message, recent_turns):
    return GuardrailVerdict(action=GuardrailAction.ALLOW, category=None, reason=None, latency_ms=1)


def _sql_tool_block(tool_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use", name="run_census_sql", input={"sql": "SELECT 1"}, id=tool_id
    )


class _QueuedRunTool:
    """Fakes _run_tool across successive calls in a multi-turn tool loop —
    each call pops the next scripted outcome (a raised exception or a
    returned payload)."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)

    def __call__(self, name, tool_input):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_REJECTED = SqlRejected(
    SqlGateResult(ok=False, sql="", violations=[SqlViolation.TABLE_NOT_ALLOWED], detail="table not allowed")
)


def test_recovery_stops_after_max_retries_of_sql_rejected(monkeypatch):
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([_REJECTED, _REJECTED]))

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
        for _ in range(MAX_RECOVERY_RETRIES)
    ]
    factory = _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-recovery", "population of a made-up place?")

    # Exactly MAX_RECOVERY_RETRIES model calls — the loop must not ask the
    # model again once the recovery budget is spent.
    assert len(factory.calls) == MAX_RECOVERY_RETRIES
    event_types = [e.type for e in events]
    assert event_types == [
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOKEN,
        EventType.DONE,
    ]
    assert events[-2].data["text"]  # honest-failure text, never blank


def test_recovery_exhaustion_yields_honest_failure_naming_what_was_tried(monkeypatch):
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([_REJECTED, _REJECTED]))

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
        for _ in range(MAX_RECOVERY_RETRIES)
    ]
    _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-recovery-msg", "population of a made-up place?")

    failure_text = events[-2].data["text"]
    assert "table not allowed" in failure_text
    assert str(MAX_RECOVERY_RETRIES) in failure_text or "couldn't" in failure_text.lower()

    session = sessions.get_session("s-recovery-msg")
    assert session.messages[-1].content == failure_text


def test_zero_row_result_counts_toward_recovery_budget(monkeypatch):
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    zero_row_payload = {"columns": ["c"], "rows": [], "row_count": 0, "truncated": False, "elapsed_ms": 5}
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([zero_row_payload, _REJECTED]))

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
        for _ in range(MAX_RECOVERY_RETRIES)
    ]
    factory = _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-recovery-zero", "population of a made-up place?")

    assert len(factory.calls) == MAX_RECOVERY_RETRIES
    assert events[-1].type == EventType.DONE
    assert events[-2].type == EventType.TOKEN


def test_generic_execution_error_also_counts_toward_recovery_budget(monkeypatch):
    """A real Snowflake execution error (e.g. a bad column reference inside
    an allowlisted table) isn't a SqlRejected — it's caught by the generic
    `except Exception` branch in agent_turn. It must still spend the
    recovery budget (D-013): a live run showed the original SqlRejected-only
    scoping leaving genuine execution failures free to retry unbounded."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    monkeypatch.setattr(
        agent, "_run_tool", _QueuedRunTool([RuntimeError("invalid identifier 'B01003E1'"), _REJECTED])
    )

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
        for _ in range(MAX_RECOVERY_RETRIES)
    ]
    factory = _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-recovery-generic-error", "population of a made-up place?")

    assert len(factory.calls) == MAX_RECOVERY_RETRIES
    assert events[-1].type == EventType.DONE
    assert "internal error" in events[-2].data["text"]


def test_generic_execution_error_text_never_leaks_into_client_facing_message(monkeypatch):
    """Code review of issue #12 (BLOCKING): a raw driver/Snowflake exception
    string can carry internal detail (host/session/query context), so it
    must never reach the deterministic honest-failure message, which is
    streamed to the client and persisted verbatim with no model mediation —
    src/app.py:28-30's 'user-safe text only' convention for anything
    code-generated and client-facing. The raw exception is still fine inside
    the tool_result content sent back to the model (private context the
    model uses to self-correct); this test only guards the client-facing
    path."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    secret_detail = "invalid identifier 'B01003E1' at host internal-sf-01.snowflakecomputing.com"
    monkeypatch.setattr(
        agent, "_run_tool", _QueuedRunTool([RuntimeError(secret_detail), _REJECTED])
    )

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
        for _ in range(MAX_RECOVERY_RETRIES)
    ]
    _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-recovery-leak-check", "population of a made-up place?")

    failure_text = events[-2].data["text"]
    assert secret_detail not in failure_text

    session = sessions.get_session("s-recovery-leak-check")
    assert secret_detail not in session.messages[-1].content


def test_successful_retry_does_not_consume_extra_budget_or_force_exit(monkeypatch):
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    success_payload = {
        "columns": ["c"], "rows": [{"c": 42}], "row_count": 1, "truncated": False, "elapsed_ms": 5,
    }
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([_REJECTED, success_payload]))

    first = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
    second = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t2")])
    final = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(
        monkeypatch,
        [
            _FakeStream([], first),
            _FakeStream([], second),
            _FakeStream(["Found 42."], final),
        ],
    )

    events = _collect("s-recovery-success", "total population?")

    # A recovered turn is a normal completion — no forced honest-failure
    # message, all 3 model calls happen (1 fewer than MAX_RECOVERY_RETRIES
    # would allow, since only 1 of the 2 outcomes was bad).
    assert len(factory.calls) == 3
    event_types = [e.type for e in events]
    assert event_types == [
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOKEN,
        EventType.DONE,
    ]
    assert events[-2].data["text"] == "Found 42."


def test_multiple_run_census_sql_failures_in_one_batch_can_exceed_budget(monkeypatch):
    """NON-BLOCKING per code review: recovery exhaustion is checked once per
    full model response, not per tool_use block, because the Anthropic API
    requires every tool_use in a response to get a matching tool_result
    before the next request — a batch can't exit mid-way. A response
    containing more failing run_census_sql calls than MAX_RECOVERY_RETRIES
    therefore pushes recovery_attempts past the budget by the size of that
    batch. Pinning the actual behavior here so a future change to the
    check's placement is a deliberate decision, not a silent regression."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([_REJECTED, _REJECTED, _REJECTED]))

    batch_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[_sql_tool_block("t1"), _sql_tool_block("t2"), _sql_tool_block("t3")],
    )
    factory = _install_fake_client(monkeypatch, [_FakeStream([], batch_response)])

    events = _collect("s-recovery-batch", "population of three made-up places?")

    # A single model call produced 3 failing tool_use blocks; the loop can't
    # stop mid-batch, so recovery_attempts (3) exceeds MAX_RECOVERY_RETRIES
    # (2) by the time exhaustion is checked after the batch.
    assert len(factory.calls) == 1
    assert events[-1].type == EventType.DONE
    failure_text = events[-2].data["text"]
    assert failure_text.count("run_census_sql failed") == 3


def test_recovery_budget_resets_across_separate_turns(monkeypatch):
    """issue #12 exit criterion: 'Retry count resets per user turn, not per
    session.' recovery_attempts/recovery_log are plain locals inside
    agent_turn (no module- or session-level state), so this holds by
    construction — pinned here as a regression guard. Turn 1 exhausts the
    recovery budget; turn 2 on the same session_id must still reach a
    normal tool round trip rather than inheriting turn 1's spent budget."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)

    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([_REJECTED, _REJECTED]))
    first_turn_responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
        for _ in range(MAX_RECOVERY_RETRIES)
    ]
    _install_fake_client(monkeypatch, [_FakeStream([], r) for r in first_turn_responses])
    first_events = _collect("s-recovery-reset", "population of a made-up place?")
    assert first_events[-1].type == EventType.DONE
    assert "couldn't" in first_events[-2].data["text"].lower()

    success_payload = {
        "columns": ["c"], "rows": [{"c": 7}], "row_count": 1, "truncated": False, "elapsed_ms": 5,
    }
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([success_payload]))
    second_tool_response = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t2")])
    final_response = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(
        monkeypatch, [_FakeStream([], second_tool_response), _FakeStream(["Found 7."], final_response)]
    )

    second_events = _collect("s-recovery-reset", "try again with a real place?")

    assert len(factory.calls) == 2
    assert second_events[-2].data["text"] == "Found 7."


def _geo_tool_block(tool_id: str, name: str = "Some County") -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use", name="resolve_geography", input={"name": name}, id=tool_id
    )


_AMBIGUOUS_GEO = {
    "query": "Washington County",
    "candidates": [
        {"geo_id": "24043", "name": "Washington County, Maryland", "level": "county", "state": "MD"},
        {"geo_id": "51191", "name": "Washington County, Virginia", "level": "county", "state": "VA"},
    ],
    "ambiguous": True,
}

_UNAMBIGUOUS_GEO = {
    "query": "Travis County, Texas",
    "candidates": [{"geo_id": "48453", "name": "Travis County, Texas", "level": "county", "state": "TX"}],
    "ambiguous": False,
}


def test_ambiguous_geography_blocks_sql_and_forces_clarifying_question(monkeypatch):
    """CLAUDE.md rule 10 / issue #13: ambiguous geography MUST prompt a
    clarifying question, never a silent pick. If the model tries
    run_census_sql anyway after an ambiguous resolve_geography, the loop
    must intercept before Snowflake is ever touched and force a
    deterministic clarifying question instead — code-enforced, not a
    request the model can decline (same pattern as bounded recovery)."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)

    def _run_tool_stub(name, tool_input):
        if name == "resolve_geography":
            return _AMBIGUOUS_GEO
        raise AssertionError(f"{name} must never run while an ambiguous geography is unresolved")

    monkeypatch.setattr(agent, "_run_tool", _run_tool_stub)

    geo_response = SimpleNamespace(stop_reason="tool_use", content=[_geo_tool_block("g1", "Washington County")])
    sql_response = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
    factory = _install_fake_client(monkeypatch, [_FakeStream([], geo_response), _FakeStream([], sql_response)])

    events = _collect("s-ambiguous", "population of Washington County?")

    assert len(factory.calls) == 2  # the model was never called a 3rd time
    event_types = [e.type for e in events]
    assert event_types == [
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOOL_START,
        EventType.TOOL_END,
        EventType.TOKEN,
        EventType.DONE,
    ]
    assert events[3].data["ok"] is False  # the blocked run_census_sql attempt
    clarifying_text = events[-2].data["text"]
    assert "Washington County, Maryland" in clarifying_text
    assert "Washington County, Virginia" in clarifying_text

    session = sessions.get_session("s-ambiguous")
    assert session.messages[-1].content == clarifying_text


def test_ambiguous_geography_does_not_block_models_own_clarifying_question(monkeypatch):
    """The normal, expected path: the model itself asks the user which
    candidate they meant (end_turn, no further tool calls) — this must
    complete normally and not be treated as a violation."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    monkeypatch.setattr(agent, "_run_tool", lambda name, tool_input: _AMBIGUOUS_GEO)

    geo_response = SimpleNamespace(stop_reason="tool_use", content=[_geo_tool_block("g1", "Washington County")])
    ask_response = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(
        monkeypatch,
        [_FakeStream([], geo_response), _FakeStream(["Did you mean Maryland or Virginia?"], ask_response)],
    )

    events = _collect("s-ambiguous-ask", "population of Washington County?")

    assert len(factory.calls) == 2
    assert events[-1].type == EventType.DONE
    assert events[-2].data["text"] == "Did you mean Maryland or Virginia?"


def test_unambiguous_geography_does_not_block_subsequent_sql(monkeypatch):
    """Regression guard against over-blocking: a resolve_geography result
    with a single, unambiguous candidate must not trip the
    clarifying-question backstop."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    success_payload = {"columns": ["c"], "rows": [{"c": 5}], "row_count": 1, "truncated": False, "elapsed_ms": 5}
    monkeypatch.setattr(agent, "_run_tool", _QueuedRunTool([_UNAMBIGUOUS_GEO, success_payload]))

    geo_response = SimpleNamespace(stop_reason="tool_use", content=[_geo_tool_block("g1", "Travis County, Texas")])
    sql_response = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
    final_response = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(
        monkeypatch,
        [_FakeStream([], geo_response), _FakeStream([], sql_response), _FakeStream(["Found 5."], final_response)],
    )

    events = _collect("s-unambiguous", "population of Travis County?")

    assert len(factory.calls) == 3
    assert events[-2].data["text"] == "Found 5."


def test_unrelated_unambiguous_geography_does_not_clear_a_still_unresolved_ambiguity(monkeypatch):
    """BLOCKING code-review finding: the first implementation tracked
    pending ambiguity in a single last-write-wins variable, so an unrelated
    *unambiguous* resolve_geography call later in the same turn silently
    cleared an earlier still-unresolved ambiguity, letting a subsequent
    run_census_sql through unblocked — a real silent-pick path issue #13's
    exit criterion 2 forbids. Two resolve_geography calls in one model
    response: one ambiguous (Washington County), one not (Travis County).
    The backstop must still block run_census_sql afterward."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)

    def _run_tool_stub(name, tool_input):
        if name == "resolve_geography":
            if tool_input.get("name") == "Washington County":
                return _AMBIGUOUS_GEO
            return _UNAMBIGUOUS_GEO
        raise AssertionError(f"{name} must never run while an ambiguous geography is unresolved")

    monkeypatch.setattr(agent, "_run_tool", _run_tool_stub)

    geo_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[_geo_tool_block("g1", "Washington County"), _geo_tool_block("g2", "Travis County, Texas")],
    )
    sql_response = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
    factory = _install_fake_client(monkeypatch, [_FakeStream([], geo_response), _FakeStream([], sql_response)])

    events = _collect("s-ambiguous-mixed", "compare Washington County and Travis County?")

    assert len(factory.calls) == 2
    clarifying_text = events[-2].data["text"]
    assert "Washington County, Maryland" in clarifying_text
    assert "Washington County, Virginia" in clarifying_text


def test_unrelated_unambiguous_geography_in_a_later_iteration_does_not_clear_ambiguity(monkeypatch):
    """Same bug as above, but the two resolve_geography calls happen in
    separate model responses (separate tool-loop iterations) rather than
    the same batch — the tracked state must persist across iterations, not
    just within one."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)

    def _run_tool_stub(name, tool_input):
        if name == "resolve_geography":
            if tool_input.get("name") == "Washington County":
                return _AMBIGUOUS_GEO
            return _UNAMBIGUOUS_GEO
        raise AssertionError(f"{name} must never run while an ambiguous geography is unresolved")

    monkeypatch.setattr(agent, "_run_tool", _run_tool_stub)

    first_geo = SimpleNamespace(stop_reason="tool_use", content=[_geo_tool_block("g1", "Washington County")])
    second_geo = SimpleNamespace(stop_reason="tool_use", content=[_geo_tool_block("g2", "Travis County, Texas")])
    sql_response = SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])
    factory = _install_fake_client(
        monkeypatch, [_FakeStream([], first_geo), _FakeStream([], second_geo), _FakeStream([], sql_response)]
    )

    events = _collect("s-ambiguous-cross-iter", "compare Washington County and Travis County?")

    assert len(factory.calls) == 3
    clarifying_text = events[-2].data["text"]
    assert "Washington County, Maryland" in clarifying_text


def test_recent_turns_passed_to_guardrail_is_last_two_messages(monkeypatch):
    sessions.append_message("s-context", ChatMessage(role="user", content="turn 1"))
    sessions.append_message("s-context", ChatMessage(role="assistant", content="answer 1"))

    captured = {}

    def _capture(message, recent_turns):
        captured["recent_turns"] = recent_turns
        return GuardrailVerdict(action=GuardrailAction.ALLOW, category=None, reason=None, latency_ms=1)

    monkeypatch.setattr(agent, "classify_input", _capture)
    final_message = SimpleNamespace(stop_reason="end_turn", content=[])
    _install_fake_client(monkeypatch, [_FakeStream(["ok"], final_message)])

    _collect("s-context", "turn 2")

    assert [m.content for m in captured["recent_turns"]] == ["turn 1", "answer 1"]


class _FakeClock:
    """Fakes time.monotonic with a mutable 'now' that test-controlled tool
    stubs advance as a side effect of 'running', simulating wall-clock
    passage without depending on the exact number of time.monotonic() call
    sites inside agent_turn. Patches the real time module for the test's
    duration (monkeypatch reverts it on teardown) — no clock-injection
    abstraction exists in agent.py, and adding one just for this would be
    more machinery than the deterministic-timing TDD target (issue #14)
    actually needs."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _search_tool_block(tool_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use", name="search_census_variables", input={"query": "population"}, id=tool_id
    )


def test_watchdog_stops_further_tool_calls_once_deadline_exceeded(monkeypatch):
    """issue #14 / CLAUDE.md rule 11: TURN_DEADLINE_S is a wall-clock budget
    checked once per round boundary. Once elapsed time crosses it, no
    further model or tool calls are issued — a clean cutoff between rounds,
    never an abort of a call already in flight."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    clock = _FakeClock()
    monkeypatch.setattr(agent.time, "monotonic", clock)

    def _slow_tool(name, tool_input):
        clock.advance(30)  # each round "burns" 30s of wall clock
        return {"hits": []}

    monkeypatch.setattr(agent, "_run_tool", _slow_tool)

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_search_tool_block(f"g{i}")])
        for i in range(3)
    ]
    factory = _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-watchdog", "total population of many places?")

    # 30s -> 60s after round 2, crossing TURN_DEADLINE_S (50s); round 3's
    # model call must never happen.
    assert len(factory.calls) == 2
    assert events[-1].type == EventType.DONE
    assert events[-2].type == EventType.TOKEN


def test_watchdog_partial_answer_uses_only_this_turns_query_rows(monkeypatch):
    """CLAUDE.md rule 2: a partial answer must never fabricate a number —
    it can only use rows this turn's run_census_sql calls actually
    returned."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    clock = _FakeClock()
    monkeypatch.setattr(agent.time, "monotonic", clock)

    success_payload = {
        "columns": ["c"], "rows": [{"c": 42}], "row_count": 1, "truncated": False, "elapsed_ms": 5,
    }

    def _slow_tool(name, tool_input):
        clock.advance(55)  # already over budget after this single round
        return success_payload

    monkeypatch.setattr(agent, "_run_tool", _slow_tool)

    responses = [SimpleNamespace(stop_reason="tool_use", content=[_sql_tool_block("t1")])]
    factory = _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-watchdog-rows", "total population?")

    assert len(factory.calls) == 1  # the deadline was already blown before round 2
    partial_text = events[-2].data["text"]
    assert "42" in partial_text

    session = sessions.get_session("s-watchdog-rows")
    assert session.messages[-1].content == partial_text


def test_watchdog_honest_message_when_no_rows_collected(monkeypatch):
    """The 'or an honest ran-out-of-time message if none' half of issue
    #14's exit criteria — no query ever succeeded this turn, so there's
    nothing groundable to report."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    clock = _FakeClock()
    monkeypatch.setattr(agent.time, "monotonic", clock)

    def _slow_tool(name, tool_input):
        clock.advance(55)
        return {"hits": []}

    monkeypatch.setattr(agent, "_run_tool", _slow_tool)

    responses = [SimpleNamespace(stop_reason="tool_use", content=[_search_tool_block("g1")])]
    _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-watchdog-empty", "total population?")

    partial_text = events[-2].data["text"]
    assert "ran out of time" in partial_text.lower()


def test_watchdog_does_not_affect_a_turn_well_under_the_deadline(monkeypatch):
    """Regression guard against premature cutoff: a normal, fast turn must
    complete exactly as it would without the watchdog."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    clock = _FakeClock()
    monkeypatch.setattr(agent.time, "monotonic", clock)
    monkeypatch.setattr(agent, "_run_tool", lambda name, tool_input: {"hits": []})

    tool_response = SimpleNamespace(stop_reason="tool_use", content=[_search_tool_block("g1")])
    final_response = SimpleNamespace(stop_reason="end_turn", content=[])
    factory = _install_fake_client(
        monkeypatch, [_FakeStream([], tool_response), _FakeStream(["done."], final_response)]
    )

    events = _collect("s-watchdog-fast", "total population?")

    assert len(factory.calls) == 2
    assert events[-2].data["text"] == "done."


def test_watchdog_fires_before_any_tool_round_when_deadline_already_exceeded(monkeypatch):
    """Edge case not covered by the other watchdog tests, which all reach
    the deadline via at least one completed round: if wall-clock time is
    already past TURN_DEADLINE_S before the very first model call, the
    watchdog must fire immediately — zero tool calls, zero model calls, the
    honest no-rows-collected fallback."""
    clock = _FakeClock()
    monkeypatch.setattr(agent.time, "monotonic", clock)

    def _allow_and_burn_time(message, recent_turns):
        clock.advance(60)  # simulates wall-clock time already spent before the loop starts
        return GuardrailVerdict(action=GuardrailAction.ALLOW, category=None, reason=None, latency_ms=1)

    monkeypatch.setattr(agent, "classify_input", _allow_and_burn_time)

    def _tool_should_not_run(name, tool_input):
        raise AssertionError("no tool should ever run once the deadline is already blown")

    monkeypatch.setattr(agent, "_run_tool", _tool_should_not_run)

    def _stream_should_not_be_called(**kwargs):
        raise AssertionError("no model call should ever happen once the deadline is already blown")

    monkeypatch.setattr(
        agent, "_client", SimpleNamespace(messages=SimpleNamespace(stream=_stream_should_not_be_called))
    )

    events = _collect("s-watchdog-zero-round", "total population?")

    assert events[-1].type == EventType.DONE
    partial_text = events[-2].data["text"]
    assert "ran out of time" in partial_text.lower()


def test_watchdog_takes_precedence_over_the_tool_loop_iteration_cap(monkeypatch):
    """If the deadline is crossed right as the loop would otherwise be
    about to hit _MAX_TOOL_LOOP_ITERATIONS, the watchdog's honest partial
    answer must win — not the infra cap's generic '[Stopped after reaching
    this turn's tool-call limit.]' text, which carries no grounded
    information."""
    monkeypatch.setattr(agent, "classify_input", _allow_verdict)
    clock = _FakeClock()
    monkeypatch.setattr(agent.time, "monotonic", clock)

    per_round = 50.0 / (agent._MAX_TOOL_LOOP_ITERATIONS - 1)

    def _slow_tool(name, tool_input):
        clock.advance(per_round)
        return {"hits": []}

    monkeypatch.setattr(agent, "_run_tool", _slow_tool)

    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[_search_tool_block(f"g{i}")])
        for i in range(agent._MAX_TOOL_LOOP_ITERATIONS)
    ]
    factory = _install_fake_client(monkeypatch, [_FakeStream([], r) for r in responses])

    events = _collect("s-watchdog-last-iter", "total population of many places?")

    assert len(factory.calls) == agent._MAX_TOOL_LOOP_ITERATIONS - 1
    partial_text = events[-2].data["text"]
    assert "tool-call limit" not in partial_text
    assert "ran out of time" in partial_text.lower()
