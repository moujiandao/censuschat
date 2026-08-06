"""Tests for src/tracing.py — durable trace logging (Trace Logging + Turn
Detail tabs).

Deterministic storage logic — TDD per CLAUDE.md rule 19.

Traces are persisted to SQLite on the mounted `data/` volume rather than held
in process memory (D-023), so history survives a container restart and a
`make deploy`. Every test points TRACE_DB_PATH at tmp_path, so nothing here
touches the real store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.tracing as tracing
from src.tracing import (
    TraceSpan,
    TurnTrace,
    get_traces,
    list_recent_sessions,
    record_turn_trace,
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own database file."""
    monkeypatch.setattr(tracing, "TRACE_DB_PATH", tmp_path / "traces.sqlite3")


def _trace(session_id: str, message: str = "hi", total_ms: int = 100) -> TurnTrace:
    return TurnTrace(
        session_id=session_id,
        user_message=message,
        started_at=datetime.now(timezone.utc),
        total_ms=total_ms,
        spans=[TraceSpan(name="guardrail", latency_ms=10, ok=True, meta={"verdict": "allow"})],
    )


def test_get_traces_empty_for_unknown_session():
    assert get_traces("s-nonexistent") == []


def test_record_and_retrieve_round_trip():
    record_turn_trace(_trace("s-a"))
    result = get_traces("s-a")
    assert len(result) == 1
    assert result[0].user_message == "hi"
    assert result[0].spans[0].name == "guardrail"
    assert result[0].spans[0].meta == {"verdict": "allow"}


def test_traces_are_isolated_per_session():
    record_turn_trace(_trace("s-a", "turn a"))
    record_turn_trace(_trace("s-b", "turn b"))
    assert [t.user_message for t in get_traces("s-a")] == ["turn a"]
    assert [t.user_message for t in get_traces("s-b")] == ["turn b"]


def test_traces_preserve_insertion_order():
    record_turn_trace(_trace("s-a", "first"))
    record_turn_trace(_trace("s-a", "second"))
    record_turn_trace(_trace("s-a", "third"))
    assert [t.user_message for t in get_traces("s-a")] == ["first", "second", "third"]


def test_traces_survive_a_process_restart():
    """The whole point of D-023. Simulates a restart by dropping every
    in-process connection and reading the file back cold."""
    record_turn_trace(_trace("s-a", "before restart"))

    # A fresh read goes to the file, not to any cached state.
    tracing._reset_connection_for_tests()

    result = get_traces("s-a")
    assert [t.user_message for t in result] == ["before restart"]


def test_no_per_session_cap():
    """D-023: history is kept in full. A reviewer clicking back through a
    long session must not silently lose their earliest turns."""
    for i in range(50):
        record_turn_trace(_trace("s-a", f"turn {i}"))
    result = get_traces("s-a")
    assert len(result) == 50
    assert result[0].user_message == "turn 0"
    assert result[-1].user_message == "turn 49"


def test_spans_round_trip_with_full_fidelity():
    """Turn Detail renders from these spans, so args and result summaries
    have to survive serialization intact — not just span names."""
    trace = TurnTrace(
        session_id="s-a",
        user_message="population of Wyoming?",
        started_at=datetime.now(timezone.utc),
        total_ms=1234,
        spans=[
            TraceSpan(
                name="tool:run_census_sql",
                latency_ms=880,
                ok=True,
                meta={
                    "args_preview": '{"sql": "SELECT SUM(\\"B01003e1\\") FROM t"}',
                    "row_count": 1,
                    "columns": ["POP"],
                    "first_row": {"POP": 581348},
                },
            ),
        ],
    )
    record_turn_trace(trace)

    span = get_traces("s-a")[0].spans[0]
    assert span.name == "tool:run_census_sql"
    assert span.latency_ms == 880
    assert span.meta["row_count"] == 1
    assert span.meta["first_row"] == {"POP": 581348}
    assert span.meta["columns"] == ["POP"]


def test_started_at_round_trips_as_an_aware_datetime():
    started = datetime.now(timezone.utc)
    record_turn_trace(
        TurnTrace(
            session_id="s-a",
            user_message="hi",
            started_at=started,
            total_ms=5,
            spans=[],
        )
    )
    got = get_traces("s-a")[0].started_at
    assert got.tzinfo is not None
    assert abs((got - started).total_seconds()) < 1


def test_a_trace_with_no_spans_round_trips():
    """A guardrail refusal produces a real turn with few or no tool spans —
    it must still appear in history rather than being dropped."""
    record_turn_trace(
        TurnTrace(
            session_id="s-a",
            user_message="what's the weather?",
            started_at=datetime.now(timezone.utc),
            total_ms=1400,
            spans=[],
        )
    )
    result = get_traces("s-a")
    assert len(result) == 1
    assert result[0].spans == []


def test_get_traces_returns_copies_not_handles_into_the_store():
    record_turn_trace(_trace("s-a"))
    result = get_traces("s-a")
    result.append(_trace("s-a", "injected"))
    assert len(get_traces("s-a")) == 1


def test_record_turn_trace_never_raises_on_internal_error(monkeypatch):
    """A tracing bug must never break a chat turn — record_turn_trace
    swallows any internal failure. Unchanged contract from the in-memory
    version, and more important now that a disk write can fail."""
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(tracing, "_connect", _boom)
    record_turn_trace(_trace("s-a"))  # must not raise


def test_get_traces_returns_empty_rather_than_raising_on_a_broken_store(monkeypatch):
    """Losing trace history is a degradation; a 500 on the Trace tab would
    be an outage. Reads fail soft for the same reason writes do."""
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(tracing, "_connect", _boom)
    assert get_traces("s-a") == []


def test_a_corrupt_row_does_not_poison_the_whole_session(monkeypatch):
    """One unparseable spans blob must cost that turn, not the history."""
    record_turn_trace(_trace("s-a", "good one"))

    conn = tracing._connect()
    conn.execute(
        "INSERT INTO traces (session_id, user_message, started_at, total_ms, spans) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s-a", "corrupt one", datetime.now(timezone.utc).isoformat(), 1, "{not json"),
    )
    conn.commit()

    result = get_traces("s-a")
    assert [t.user_message for t in result] == ["good one"]


# ---------------------------------------------------------------------------
# Cross-session listing — what makes "show me the historical runs" possible
# ---------------------------------------------------------------------------

def test_list_recent_sessions_empty_when_nothing_recorded():
    assert list_recent_sessions() == []


def test_list_recent_sessions_summarises_each_session():
    record_turn_trace(_trace("s-a", "first a"))
    record_turn_trace(_trace("s-a", "second a"))
    record_turn_trace(_trace("s-b", "only b"))

    sessions = list_recent_sessions()
    by_id = {s["session_id"]: s for s in sessions}

    assert by_id["s-a"]["turns"] == 2
    assert by_id["s-b"]["turns"] == 1
    # The most recent message is the useful label for picking a session.
    assert by_id["s-a"]["last_message"] == "second a"


def test_list_recent_sessions_is_newest_first():
    record_turn_trace(_trace("s-old", "old"))
    record_turn_trace(_trace("s-new", "new"))
    assert [s["session_id"] for s in list_recent_sessions()][0] == "s-new"


def test_list_recent_sessions_respects_its_limit():
    for i in range(10):
        record_turn_trace(_trace(f"s-{i}"))
    assert len(list_recent_sessions(limit=4)) == 4


def test_list_recent_sessions_fails_soft(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(tracing, "_connect", _boom)
    assert list_recent_sessions() == []


def test_the_suite_never_writes_to_the_real_trace_store():
    """Pins the repo-root conftest fixture.

    This is a regression guard, not a unit test. When traces moved from a
    process dict to a file (D-023), every test that exercised `agent_turn`
    silently began writing into `data/traces.sqlite3` — the store the running
    app reads — so real turn history filled up with `s-watchdog` and
    `s-refuse`. Nothing failed; it was only visible by opening the tab.
    """
    assert tracing.TRACE_DB_PATH != Path("data/traces.sqlite3")
    assert "data/traces.sqlite3" not in str(tracing.TRACE_DB_PATH)
