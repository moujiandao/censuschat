"""In-app trace logging — a lightweight stand-in for the full Langfuse
integration (issue #18, deliberately deferred; see docs/reflection.md for
the tradeoff). Records span-level detail per turn — guardrail latency,
each model call's token usage, each tool call's latency, args and result
digest, final answer, and terminal status for the Evidence tab to render.

**Durable (D-023).** Traces are written to SQLite on the same mounted
`data/` volume as the session store, so history survives a container
restart and a `make deploy`. The previous version kept them in a process
dictionary, which meant every deploy silently erased the observability
data — the moment you most want to compare before and after is exactly the
moment it was thrown away.

Still deliberately NOT the session store (`src/sessions.py`): different
lifecycle, different schema, and a chat turn must not fail because a trace
write did. It is also still single-instance — a SQLite file on a local
volume, so a second replica would not share it. That closes the restart
gap, not the distributed one; rule 17 remains unmet (D-021).

Two invariants this module holds, both about staying out of the way:

  1. **Recording never raises into `agent_turn`.** A tracing bug is not
     allowed to break a chat turn, and a disk write can fail in ways a
     dict assignment could not.
  2. **Reads fail soft.** Losing trace history is a degradation; a 500 on
     the Trace tab would be an outage.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TRACE_DB_PATH = Path(os.environ.get("TRACE_DB_PATH", "data/traces.sqlite3"))

# No per-session cap. History is kept in full: a reviewer clicking back
# through a long session must not silently lose their earliest turns, and a
# few hundred turns is a trivially small SQLite file. If this ever runs
# somewhere unbounded, retention becomes a real decision rather than a
# constant.

_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS traces ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "session_id TEXT NOT NULL, "
    "user_message TEXT NOT NULL, "
    "final_answer TEXT NOT NULL DEFAULT '', "
    "terminal_status TEXT NOT NULL DEFAULT 'done', "
    "started_at TEXT NOT NULL, "
    "total_ms INTEGER NOT NULL, "
    "spans TEXT NOT NULL)"
)

# Reads filter by session and order by id; without this every Evidence load
# is a full scan once history accumulates.
_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_traces_session ON traces (session_id, id)"
)

_lock = threading.Lock()


class TraceSpan(BaseModel):
    name: str  # "guardrail", "model_call_1", "tool:run_census_sql"
    latency_ms: int
    ok: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class TurnTrace(BaseModel):
    session_id: str
    user_message: str
    final_answer: str = ""
    terminal_status: Literal["done", "error"] = "done"
    started_at: datetime
    total_ms: int
    spans: list[TraceSpan] = Field(default_factory=list)


def _connect() -> sqlite3.Connection:
    TRACE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TRACE_DB_PATH)
    conn.execute(_CREATE_TABLE_SQL)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(traces)")}
    migrated = False
    if "final_answer" not in columns:
        conn.execute(
            "ALTER TABLE traces ADD COLUMN "
            "final_answer TEXT NOT NULL DEFAULT ''"
        )
        migrated = True
    if "terminal_status" not in columns:
        conn.execute(
            "ALTER TABLE traces ADD COLUMN "
            "terminal_status TEXT NOT NULL DEFAULT 'done'"
        )
        migrated = True
    conn.execute(_CREATE_INDEX_SQL)
    if migrated:
        conn.commit()
    return conn


def _reset_connection_for_tests() -> None:
    """No-op hook kept so tests can express "now read it back cold" without
    depending on whether this module caches a connection. It does not today;
    if that ever changes, the restart test keeps working unmodified."""
    return None


def record_turn_trace(trace: TurnTrace) -> None:
    """Persist one turn's spans. Never raises into the caller — logs and
    swallows instead, so a tracing bug can be diagnosed from server logs
    without ever breaking a chat turn (CLAUDE.md rule 11)."""
    try:
        spans_json = json.dumps([span.model_dump(mode="json") for span in trace.spans])
        with _lock:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO traces "
                    "(session_id, user_message, final_answer, terminal_status, "
                    "started_at, total_ms, spans) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        trace.session_id,
                        trace.user_message,
                        trace.final_answer,
                        trace.terminal_status,
                        trace.started_at.isoformat(),
                        trace.total_ms,
                        spans_json,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        logger.warning(
            "record_turn_trace failed for session_id=%s", trace.session_id, exc_info=True
        )


def get_traces(session_id: str) -> list[TurnTrace]:
    """Every recorded turn for one session, oldest first. Returns [] rather
    than raising if the store is unreadable."""
    try:
        with _lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT user_message, final_answer, terminal_status, "
                    "started_at, total_ms, spans FROM traces "
                    "WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
            finally:
                conn.close()
    except Exception:
        logger.warning("get_traces failed for session_id=%s", session_id, exc_info=True)
        return []

    traces: list[TurnTrace] = []
    for (
        user_message,
        final_answer,
        terminal_status,
        started_at,
        total_ms,
        spans_json,
    ) in rows:
        try:
            spans = [TraceSpan(**s) for s in json.loads(spans_json)]
            traces.append(
                TurnTrace(
                    session_id=session_id,
                    user_message=user_message,
                    final_answer=final_answer,
                    terminal_status=terminal_status,
                    started_at=datetime.fromisoformat(started_at),
                    total_ms=total_ms,
                    spans=spans,
                )
            )
        except Exception:
            # One unparseable row costs that turn, not the session's history.
            logger.warning(
                "skipping unreadable trace row for session_id=%s", session_id, exc_info=True
            )
    return traces


def list_recent_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """One summary row per session that has any recorded traces, newest
    first — what lets the UI offer history from *previous* visits rather
    than only the session in this browser tab.

    `last_message` is the most recent user message, which is the only label
    that makes a bare session id pickable by a human.
    """
    try:
        with _lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT session_id, COUNT(*), MAX(id) AS last_id "
                    "FROM traces GROUP BY session_id "
                    "ORDER BY last_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                summaries = []
                for session_id, turns, last_id in rows:
                    last = conn.execute(
                        "SELECT user_message, started_at FROM traces WHERE id = ?",
                        (last_id,),
                    ).fetchone()
                    summaries.append(
                        {
                            "session_id": session_id,
                            "turns": turns,
                            "last_message": last[0] if last else "",
                            "last_at": last[1] if last else "",
                        }
                    )
                return summaries
            finally:
                conn.close()
    except Exception:
        logger.warning("list_recent_sessions failed", exc_info=True)
        return []
