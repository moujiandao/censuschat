"""Session store — src/contracts.py:get_session/append_message (issue #8).

Full-history replay from SQLite, keyed by session_id (CLAUDE.md rule 16).
No summarization, no extraction. Only user/assistant messages are ever
persisted — ChatMessage.role has no tool-call literal by design (PRD §6):
tool results live only inside the turn that produced them and are never
written to the store, which is what keeps replay cheap.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.contracts import ChatMessage, Session

SESSION_DB_PATH = Path(os.environ.get("SESSION_DB_PATH", "data/sessions.sqlite3"))

_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS messages ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "session_id TEXT NOT NULL, "
    "role TEXT NOT NULL, "
    "content TEXT NOT NULL, "
    "created_at TEXT NOT NULL)"
)


def _sqlite_connect() -> sqlite3.Connection:
    SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SESSION_DB_PATH)
    conn.execute(_CREATE_TABLE_SQL)
    return conn


def get_session(session_id: str) -> Session:
    """Fetch (or create empty) session. Full history replay is the state
    model — no summarization, no extraction."""
    conn = _sqlite_connect()
    try:
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    messages = [
        ChatMessage(
            role=role, content=content, created_at=datetime.fromisoformat(created_at)
        )
        for role, content, created_at in rows
    ]
    return Session(session_id=session_id, messages=messages)


def append_message(session_id: str, msg: ChatMessage) -> None:
    created_at = msg.created_at or datetime.now(timezone.utc)
    conn = _sqlite_connect()
    try:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, msg.role, msg.content, created_at.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
