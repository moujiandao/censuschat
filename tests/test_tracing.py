"""Tests for src/tracing.py — in-app trace logging (Trace Logging tab).

Deterministic, in-memory storage logic — TDD per CLAUDE.md rule 19.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import src.tracing as tracing
from src.tracing import TraceSpan, TurnTrace, get_traces, record_turn_trace


def _trace(session_id: str, message: str = "hi") -> TurnTrace:
    return TurnTrace(
        session_id=session_id,
        user_message=message,
        started_at=datetime.now(timezone.utc),
        total_ms=100,
        spans=[TraceSpan(name="guardrail", latency_ms=10, ok=True, meta={"verdict": "allow"})],
    )


def test_get_traces_empty_for_unknown_session():
    assert get_traces("s-nonexistent") == []


def test_record_and_retrieve_round_trip(monkeypatch):
    monkeypatch.setattr(tracing, "_traces", defaultdict(list))
    trace = _trace("s-a")
    record_turn_trace(trace)
    result = get_traces("s-a")
    assert len(result) == 1
    assert result[0].user_message == "hi"
    assert result[0].spans[0].name == "guardrail"


def test_traces_are_isolated_per_session(monkeypatch):
    monkeypatch.setattr(tracing, "_traces", defaultdict(list))
    record_turn_trace(_trace("s-a", "turn a"))
    record_turn_trace(_trace("s-b", "turn b"))
    assert [t.user_message for t in get_traces("s-a")] == ["turn a"]
    assert [t.user_message for t in get_traces("s-b")] == ["turn b"]


def test_traces_preserve_insertion_order(monkeypatch):
    monkeypatch.setattr(tracing, "_traces", defaultdict(list))
    record_turn_trace(_trace("s-a", "first"))
    record_turn_trace(_trace("s-a", "second"))
    record_turn_trace(_trace("s-a", "third"))
    assert [t.user_message for t in get_traces("s-a")] == ["first", "second", "third"]


def test_traces_capped_per_session_dropping_oldest_first(monkeypatch):
    monkeypatch.setattr(tracing, "_traces", defaultdict(list))
    monkeypatch.setattr(tracing, "_MAX_TRACES_PER_SESSION", 3)
    for i in range(5):
        record_turn_trace(_trace("s-a", f"turn {i}"))
    result = get_traces("s-a")
    assert len(result) == 3
    assert [t.user_message for t in result] == ["turn 2", "turn 3", "turn 4"]


def test_get_traces_returns_a_copy_not_the_live_list(monkeypatch):
    """Mutating the returned list must never corrupt internal state —
    get_traces is a read, not a handle into the store."""
    monkeypatch.setattr(tracing, "_traces", defaultdict(list))
    record_turn_trace(_trace("s-a"))
    result = get_traces("s-a")
    result.append(_trace("s-a", "injected"))
    assert len(get_traces("s-a")) == 1


def test_record_turn_trace_never_raises_on_internal_error(monkeypatch):
    """A tracing bug must never break a chat turn — record_turn_trace
    swallows any internal failure."""
    def _broken_dict_get(*args, **kwargs):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(tracing, "_traces", _BrokenDefaultDict())
    record_turn_trace(_trace("s-a"))  # must not raise


class _BrokenDefaultDict(dict):
    def __getitem__(self, key):
        raise RuntimeError("simulated internal failure")
