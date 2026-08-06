"""In-app trace logging — a lightweight stand-in for the full Langfuse
integration (issue #18, deliberately deferred; see docs/reflection.md for
the tradeoff). Records span-level detail per turn — guardrail latency,
each model call's token usage, each tool call's latency — in memory,
keyed by session_id, for the Trace Logging tab to render.

Deliberately NOT the session store (src/sessions.py, SQLite, durable):
this is observability data, lost on restart, capped per session so it
can't grow unbounded over a long-running process. Recording a trace must
never raise into agent_turn or affect what the user sees — a tracing bug
is not allowed to break a chat turn.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_TRACES_PER_SESSION = 20


class TraceSpan(BaseModel):
    name: str  # "guardrail", "model_call_1", "tool:run_census_sql"
    latency_ms: int
    ok: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class TurnTrace(BaseModel):
    session_id: str
    user_message: str
    started_at: datetime
    total_ms: int
    spans: list[TraceSpan] = Field(default_factory=list)


_lock = threading.Lock()
_traces: dict[str, list[TurnTrace]] = defaultdict(list)


def record_turn_trace(trace: TurnTrace) -> None:
    """Never raises into the caller — logs and swallows instead, so a
    tracing bug can be diagnosed from server logs without ever breaking a
    chat turn. Appends to this session's trace list, trimming the oldest
    entries once _MAX_TRACES_PER_SESSION is exceeded."""
    try:
        with _lock:
            session_traces = _traces[trace.session_id]
            session_traces.append(trace)
            overflow = len(session_traces) - _MAX_TRACES_PER_SESSION
            if overflow > 0:
                del session_traces[:overflow]
    except Exception:
        logger.warning("record_turn_trace failed for session_id=%s", trace.session_id, exc_info=True)


def get_traces(session_id: str) -> list[TurnTrace]:
    with _lock:
        return list(_traces.get(session_id, []))
