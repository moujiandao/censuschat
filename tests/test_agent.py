"""Tests for the agent loop — src/contracts.py:agent_turn (issues #7, #11).

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
from src.contracts import ChatMessage, EventType, GuardrailAction, GuardrailVerdict, RefusalCategory


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
