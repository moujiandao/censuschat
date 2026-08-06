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
from src.contracts import ChatEvent, EventType, SnapshotError

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


def test_health_endpoint_returns_health_report(monkeypatch):
    monkeypatch.setattr(
        "src.app.health_report", lambda: {"status": "ok", "snapshot": "ok", "snowflake": "ok"}
    )
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "snapshot": "ok", "snowflake": "ok"}


def test_health_endpoint_reports_degraded(monkeypatch):
    monkeypatch.setattr(
        "src.app.health_report",
        lambda: {"status": "degraded", "snapshot": "missing", "snowflake": "unreachable"},
    )
    response = client.get("/api/health")
    assert response.json()["status"] == "degraded"


def test_startup_survives_snapshot_error_and_reports_degraded(monkeypatch):
    """issue #15 exit criteria 1/2: a SnapshotError at boot must not crash
    the app — it must still start and /api/health must report degraded."""

    def _raise(force=False):
        raise SnapshotError("boom")

    monkeypatch.setattr("src.app.build_snapshot", _raise)
    monkeypatch.setattr("src.app.check_snowflake_reachability", lambda: False)
    monkeypatch.setattr(
        "src.app.health_report",
        lambda: {"status": "degraded", "snapshot": "missing", "snowflake": "unreachable"},
    )

    with TestClient(app) as booted_client:
        response = booted_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_startup_checks_snowflake_reachability_exactly_once(monkeypatch):
    """CLAUDE.md rule 13: Snowflake reachability is a boot-time-only check
    (src/health.py). Confirms the lifespan handler actually calls it, since
    /api/health and agent_turn now both rely entirely on this happening at
    startup rather than probing live themselves."""
    calls = []
    monkeypatch.setattr("src.app.build_snapshot", lambda force=False: None)
    monkeypatch.setattr("src.app.check_snowflake_reachability", lambda: calls.append(1) or True)
    monkeypatch.setattr(
        "src.app.health_report", lambda: {"status": "ok", "snapshot": "ok", "snowflake": "ok"}
    )

    with TestClient(app):
        pass

    assert len(calls) == 1


def test_startup_with_healthy_snapshot_does_not_crash(monkeypatch):
    """Regression guard: a normal boot (build_snapshot succeeds) must not
    be affected by the lifespan handler."""
    monkeypatch.setattr("src.app.build_snapshot", lambda force=False: None)
    monkeypatch.setattr("src.app.check_snowflake_reachability", lambda: True)
    monkeypatch.setattr(
        "src.app.health_report", lambda: {"status": "ok", "snapshot": "ok", "snowflake": "ok"}
    )

    with TestClient(app) as booted_client:
        response = booted_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_evals_endpoint_returns_none_when_no_results_exist(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    response = client.get("/api/evals")
    assert response.status_code == 200
    assert response.json() == {"latest": None, "previous": None}


def test_evals_endpoint_returns_latest_run(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = {"run_at": "2026-08-06T00:00:00Z", "git_sha": "abc123", "results": [], "pass_rate": 1.0, "by_category": {}}
    (tmp_path / "latest.json").write_text(json.dumps(run))

    response = client.get("/api/evals")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["git_sha"] == "abc123"
    assert body["previous"] is None


def test_evals_endpoint_returns_previous_run_when_two_distinct_runs_exist(tmp_path, monkeypatch):
    """latest.json always mirrors the newest timestamped file (that's the
    invariant evals/run_evals.py maintains), so distinguishing
    "one run ever" from "a real previous run" requires two *distinct*
    timestamped files, not just one alongside latest.json."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    older = {"run_at": "2026-08-05T00:00:00Z", "git_sha": "old111", "results": [], "pass_rate": 0.5, "by_category": {}}
    newer = {"run_at": "2026-08-06T00:00:00Z", "git_sha": "new222", "results": [], "pass_rate": 0.9, "by_category": {}}
    (tmp_path / "20260805T000000Z.json").write_text(json.dumps(older))
    (tmp_path / "20260806T000000Z.json").write_text(json.dumps(newer))
    (tmp_path / "latest.json").write_text(json.dumps(newer))

    response = client.get("/api/evals")

    body = response.json()
    assert body["latest"]["git_sha"] == "new222"
    assert body["previous"]["git_sha"] == "old111"


def test_traces_endpoint_returns_empty_list_for_unknown_session():
    response = client.get("/api/traces", params={"session_id": "s-unknown"})
    assert response.status_code == 200
    assert response.json() == {"traces": []}


def test_traces_endpoint_returns_recorded_traces(monkeypatch):
    from collections import defaultdict
    from datetime import datetime, timezone

    from src.tracing import TraceSpan, TurnTrace, record_turn_trace

    monkeypatch.setattr("src.tracing._traces", defaultdict(list))
    record_turn_trace(
        TurnTrace(
            session_id="s-with-traces",
            user_message="population of Wyoming?",
            started_at=datetime.now(timezone.utc),
            total_ms=250,
            spans=[TraceSpan(name="guardrail", latency_ms=10, ok=True, meta={"verdict": "allow"})],
        )
    )

    response = client.get("/api/traces", params={"session_id": "s-with-traces"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["traces"]) == 1
    assert body["traces"][0]["user_message"] == "population of Wyoming?"
    assert body["traces"][0]["spans"][0]["name"] == "guardrail"


def test_traces_endpoint_requires_session_id_query_param():
    response = client.get("/api/traces")
    assert response.status_code == 422
