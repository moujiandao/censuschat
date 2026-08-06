"""Tests for the agent loop — src/contracts.py:agent_turn (issues #7, #11, #12).

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
