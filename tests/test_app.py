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

import src.tracing as tracing
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
    traces = tracing.get_traces("s2")
    assert len(traces) == 1
    assert traces[0].terminal_status == "error"
    assert traces[0].final_answer == events[-1]["data"]["message"]
    assert "simulated Snowflake" not in traces[0].model_dump_json()
    assert "RuntimeError" not in traces[0].model_dump_json()


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
    assert response.json() == {"latest": None, "history": []}


def test_evals_endpoint_returns_latest_run(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = {"run_at": "2026-08-06T00:00:00Z", "git_sha": "abc123", "results": [], "pass_rate": 1.0, "by_category": {}}
    (tmp_path / "latest.json").write_text(json.dumps(run))

    response = client.get("/api/evals")

    assert response.status_code == 200
    assert response.json()["latest"]["git_sha"] == "abc123"


def test_evals_history_never_double_counts_latest_json(tmp_path, monkeypatch):
    """latest.json is a copy of the newest timestamped file. Counting it as
    its own run would show a phantom extra column on a view whose entire job
    is "are we improving," which is the worst place for a miscount."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    newer = {"run_at": "2026-08-06T00:00:00Z", "git_sha": "new222", "results": [], "pass_rate": 0.9, "by_category": {}}
    (tmp_path / "20260806T000000Z.json").write_text(json.dumps(newer))
    (tmp_path / "latest.json").write_text(json.dumps(newer))

    history = client.get("/api/evals").json()["history"]

    assert len(history) == 1


def test_evals_history_is_oldest_first_with_per_scenario_outcomes(tmp_path, monkeypatch):
    """History needs run order and an outcome per scenario per run. It does
    not need full results, which is why the endpoint returns a projection."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    older = _run_with(["DF-01"])
    older["git_sha"] = "old111"
    newer = _run_with(["DF-01", "PM-08"])
    newer["git_sha"] = "new222"
    newer["results"][1]["passed"] = False
    (tmp_path / "20260805T000000Z.json").write_text(json.dumps(older))
    (tmp_path / "20260806T000000Z.json").write_text(json.dumps(newer))
    (tmp_path / "latest.json").write_text(json.dumps(newer))

    history = client.get("/api/evals").json()["history"]

    assert [h["git_sha"] for h in history] == ["old111", "new222"]
    assert history[0]["scenarios"] == {"DF-01": "pass"}
    assert history[1]["scenarios"] == {"DF-01": "pass", "PM-08": "fail"}


def test_evals_history_excludes_rows_an_older_run_recorded_as_pending(
    tmp_path, monkeypatch
):
    """A scenario that never ran must not appear in history. An unrun row is
    absence of evidence, not a failure."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = _run_with(["DF-01", "OLD-99"])
    run["results"][1]["status"] = "pending"
    (tmp_path / "20260806T000000Z.json").write_text(json.dumps(run))
    (tmp_path / "latest.json").write_text(json.dumps(run))

    history = client.get("/api/evals").json()["history"]

    assert history[0]["scenarios"] == {"DF-01": "pass"}


def _run_with(scenario_ids: list[str]) -> dict:
    return {
        "run_at": "2026-08-06T00:00:00Z",
        "git_sha": "abc123",
        "results": [
            {
                "scenario_id": sid,
                "category": "direct_fact",
                "passed": True,
                "checks": [],
                "answer_final": "",
                "elapsed_s": 1.0,
                "status": "executed",
            }
            for sid in scenario_ids
        ],
        "pass_rate": 1.0,
        "by_category": {},
    }


def test_evals_endpoint_attaches_the_question_a_stored_result_does_not_carry(
    tmp_path, monkeypatch
):
    """A stored EvalResult records what happened, not what was asked, so the
    Evals tab could only render an opaque id without this join."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    (tmp_path / "latest.json").write_text(json.dumps(_run_with(["DF-01"])))

    row = client.get("/api/evals").json()["latest"]["results"][0]

    assert row["turns"] == ["Population of Alameda County, California?"]
    assert row["notes"]


def test_evals_endpoint_drops_rows_an_older_run_recorded_as_pending(
    tmp_path, monkeypatch
):
    """The committed result files still hold rows from when the set carried an
    unrun backlog. An unrun scenario is not evidence, so it is not shown — but
    the stored file stays as it was, rather than being rewritten."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = _run_with(["DF-01", "OLD-99"])
    run["results"][1]["status"] = "pending"
    (tmp_path / "latest.json").write_text(json.dumps(run))

    rows = client.get("/api/evals").json()["latest"]["results"]

    assert [r["scenario_id"] for r in rows] == ["DF-01"]


def test_evals_endpoint_derives_suite_and_outcome_for_historical_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = _run_with(["DF-05", "PM-08"])
    run["results"][1]["passed"] = False
    (tmp_path / "latest.json").write_text(json.dumps(run))

    latest = client.get("/api/evals").json()["latest"]
    rows = latest["results"]

    assert [(r["suite"], r["outcome"]) for r in rows] == [
        ("regression", "pass"),
        ("capability", "fail"),
    ]
    assert latest["legacy"] is True
    assert all(row["legacy"] is True for row in rows)


def test_evals_endpoint_preserves_explicit_suite_and_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = _run_with(["DF-05"])
    run["results"][0].update(
        {"suite": "capability", "outcome": "inconclusive", "passed": False}
    )
    (tmp_path / "latest.json").write_text(json.dumps(run))

    latest = client.get("/api/evals").json()["latest"]
    row = latest["results"][0]

    assert (row["suite"], row["outcome"]) == ("capability", "inconclusive")
    assert latest["legacy"] is False
    assert row["legacy"] is False


def test_evals_history_marks_pre_tri_state_artifacts_as_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    legacy = _run_with(["DF-05"])
    current = _run_with(["DF-05"])
    current["results"][0].update(
        {"suite": "regression", "outcome": "inconclusive", "passed": False}
    )
    (tmp_path / "20260805T000000Z.json").write_text(json.dumps(legacy))
    (tmp_path / "20260806T000000Z.json").write_text(json.dumps(current))
    (tmp_path / "latest.json").write_text(json.dumps(current))

    history = client.get("/api/evals").json()["history"]

    assert [run["legacy"] for run in history] == [True, False]
    assert history[1]["scenarios"] == {"DF-05": "inconclusive"}


def test_evals_endpoint_keeps_an_unknown_scenario_id_rather_than_failing(
    tmp_path, monkeypatch
):
    """An older run may name a scenario since renamed or removed. It still
    records a real run, so it is kept and rendered unlabeled."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    (tmp_path / "latest.json").write_text(json.dumps(_run_with(["GONE-99"])))

    response = client.get("/api/evals")

    assert response.status_code == 200
    row = response.json()["latest"]["results"][0]
    assert row["turns"] == []
    assert row["notes"] is None


def test_evals_history_survives_a_corrupt_result_file(tmp_path, monkeypatch):
    """One unreadable file must not take out the whole history view."""
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    (tmp_path / "20260805T000000Z.json").write_text("{ not json")
    (tmp_path / "20260806T000000Z.json").write_text(json.dumps(_run_with(["DF-05"])))
    (tmp_path / "latest.json").write_text(json.dumps(_run_with(["DF-05"])))

    history = client.get("/api/evals").json()["history"]

    assert [h["git_sha"] for h in history] == ["abc123"]


def test_evals_endpoint_still_serves_results_when_scenario_metadata_is_unavailable(
    tmp_path, monkeypatch
):
    """A deployment without evals/scenarios.py importable must still serve the
    stored results, just unlabeled. Losing the labels is a degradation; losing
    the tab is an outage."""
    import sys

    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    (tmp_path / "latest.json").write_text(json.dumps(_run_with(["DF-01"])))
    # A sys.modules entry of None makes the import inside _scenario_index
    # raise, which is the real failure shape it guards against.
    monkeypatch.setitem(sys.modules, "evals.scenarios", None)

    response = client.get("/api/evals")

    assert response.status_code == 200
    rows = response.json()["latest"]["results"]
    assert [r["scenario_id"] for r in rows] == ["DF-01"]
    assert rows[0]["turns"] == []


def test_every_golden_scenario_has_been_run():
    """The set no longer carries an unrun backlog. A scenario that has never
    been executed is a wish, not a test, and must not sit alongside ones that
    have — which is exactly the confusion this set was cleaned up to remove."""
    from evals.scenarios import GOLDEN_SCENARIOS

    assert GOLDEN_SCENARIOS
    assert all(s.status == "executed" for s in GOLDEN_SCENARIOS)


def test_traces_endpoint_returns_empty_list_for_unknown_session():
    response = client.get("/api/traces", params={"session_id": "s-unknown"})
    assert response.status_code == 200
    assert response.json() == {"traces": []}


def test_traces_endpoint_returns_recorded_traces(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from src.tracing import TraceSpan, TurnTrace, record_turn_trace

    monkeypatch.setattr("src.tracing.TRACE_DB_PATH", tmp_path / "traces.sqlite3")
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


# ---------------------------------------------------------------------------
# /api/trace-sessions — history from previous visits (D-023)
# ---------------------------------------------------------------------------

def test_trace_sessions_empty_when_nothing_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tracing.TRACE_DB_PATH", tmp_path / "traces.sqlite3")
    response = client.get("/api/trace-sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_trace_sessions_lists_sessions_newest_first(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from src.tracing import TurnTrace, record_turn_trace

    monkeypatch.setattr("src.tracing.TRACE_DB_PATH", tmp_path / "traces.sqlite3")
    for sid, msg in [("s-old", "older question"), ("s-new", "newer question")]:
        record_turn_trace(
            TurnTrace(
                session_id=sid,
                user_message=msg,
                started_at=datetime.now(timezone.utc),
                total_ms=10,
                spans=[],
            )
        )

    sessions = client.get("/api/trace-sessions").json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["s-new", "s-old"]
    assert sessions[0]["last_message"] == "newer question"
    assert sessions[0]["turns"] == 1


def test_trace_sessions_survives_a_broken_store(monkeypatch):
    """Losing history is a degradation; a 500 on the tab would be an outage."""
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr("src.tracing._connect", _boom)
    response = client.get("/api/trace-sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": []}
