"""Tests for the /api/chat streaming transport (issue #6).

Scope is deliberately narrow: the streaming/transport contract only, per
CLAUDE.md rule 11. Generative agent behavior is covered by golden evals
(PRD §7), not here — every test below stubs `agent_turn` so nothing depends
on Snowflake, Anthropic, or the guardrail classifier.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.contracts import ChatEvent, EventType

client = TestClient(app)


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), block
        events.append(json.loads(block[len("data: ") :]))
    return events


def test_stream_terminates_with_done():
    async def fake_agent_turn(session_id: str, message: str):
        yield ChatEvent(type=EventType.TOKEN, data={"text": "Alameda County has "})
        yield ChatEvent(type=EventType.TOKEN, data={"text": "1,648,556 people."})
        yield ChatEvent(type=EventType.DONE, data={"elapsed_ms": 900})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.app.agent_turn", fake_agent_turn)
        response = client.post(
            "/api/chat", json={"session_id": "s1", "message": "population of Alameda County?"}
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert len(events) == 3
    assert events[-1]["type"] == "done"


def test_mid_turn_exception_ends_stream_with_error_not_a_dropped_connection():
    async def raising_agent_turn(session_id: str, message: str):
        yield ChatEvent(type=EventType.TOKEN, data={"text": "partial answer..."})
        raise RuntimeError("simulated Snowflake connection reset — must never leak to client")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.app.agent_turn", raising_agent_turn)
        response = client.post("/api/chat", json={"session_id": "s2", "message": "anything"})

    # The exception happens after streaming has already started, so it can
    # never surface as an HTTP 500 — the connection must complete normally
    # with an honest ERROR event as the last thing on the wire.
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1]["type"] == "error"
    assert "simulated Snowflake" not in events[-1]["data"]["message"]
    assert "RuntimeError" not in events[-1]["data"]["message"]


def test_tool_call_emits_matching_start_and_end():
    async def tool_calling_agent_turn(session_id: str, message: str):
        yield ChatEvent(
            type=EventType.TOOL_START,
            data={"tool": "search_census_variables", "args_preview": "median income"},
        )
        yield ChatEvent(
            type=EventType.TOOL_END,
            data={"tool": "search_census_variables", "ok": True, "elapsed_ms": 42},
        )
        yield ChatEvent(type=EventType.DONE, data={"elapsed_ms": 500})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.app.agent_turn", tool_calling_agent_turn)
        response = client.post("/api/chat", json={"session_id": "s3", "message": "median income?"})

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types == ["tool_start", "tool_end", "done"]
    assert events[0]["data"]["tool"] == events[1]["data"]["tool"]


def test_generator_ending_without_terminal_event_still_ends_honestly():
    """Defends the contract even against a buggy agent_turn that violates
    its own contract by not emitting a terminal event (CLAUDE.md rule 11:
    every stream terminates with done or error, no silent endings)."""

    async def contract_violating_agent_turn(session_id: str, message: str):
        yield ChatEvent(type=EventType.TOKEN, data={"text": "..."})
        return

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.app.agent_turn", contract_violating_agent_turn)
        response = client.post("/api/chat", json={"session_id": "s4", "message": "anything"})

    events = _parse_sse(response.text)
    assert events[-1]["type"] == "error"
