"""Tests for the session store — src/contracts.py:get_session/append_message
(issue #8). Full-history replay from SQLite, keyed by session_id, no
summarization or extraction (CLAUDE.md rule 16). Written first per rule 19.
"""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from src.contracts import ChatMessage
import src.sessions as sessions


@pytest.fixture(autouse=True)
def isolated_session_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_DB_PATH", tmp_path / "sessions.sqlite3")


def test_get_session_on_unknown_id_returns_empty_session_not_raise():
    session = sessions.get_session("unknown-session")
    assert session.session_id == "unknown-session"
    assert session.messages == []


def test_append_then_get_returns_message_in_same_order():
    sessions.append_message("s1", ChatMessage(role="user", content="hello"))

    session = sessions.get_session("s1")

    assert len(session.messages) == 1
    assert session.messages[0].role == "user"
    assert session.messages[0].content == "hello"


def test_multi_turn_conversation_replays_in_full_order():
    turns = [
        ("user", "Population of Alameda County?"),
        ("assistant", "1.67 million."),
        ("user", "What about households?"),
        ("assistant", "560,000."),
    ]
    for role, content in turns:
        sessions.append_message("s2", ChatMessage(role=role, content=content))

    session = sessions.get_session("s2")

    assert [(m.role, m.content) for m in session.messages] == turns


def test_messages_persist_across_simulated_process_restart():
    sessions.append_message("s3", ChatMessage(role="user", content="first"))

    # Fresh connection straight to the file, bypassing the module entirely —
    # proves durability on disk, not an in-process cache.
    conn = sqlite3.connect(sessions.SESSION_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ?", ("s3",)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("user", "first")]

    # And a brand-new call through the module's own read path sees it too.
    session = sessions.get_session("s3")
    assert session.messages[0].content == "first"


def test_two_sessions_do_not_leak_into_each_other():
    sessions.append_message("s4", ChatMessage(role="user", content="session four"))
    sessions.append_message("s5", ChatMessage(role="user", content="session five"))

    assert [m.content for m in sessions.get_session("s4").messages] == ["session four"]
    assert [m.content for m in sessions.get_session("s5").messages] == ["session five"]


def test_chat_message_role_rejects_non_user_assistant_values():
    """PRD §6: no tool role by design — tool results live only inside the
    turn that produced them and must never be persisted across turns. This
    is enforced at the type level: append_message only accepts ChatMessage,
    and ChatMessage itself refuses any role outside user/assistant."""
    with pytest.raises(ValidationError):
        ChatMessage(role="tool", content="tool result")


def test_storage_schema_has_no_tool_call_columns():
    sessions.append_message("s6", ChatMessage(role="assistant", content="hi"))

    conn = sqlite3.connect(sessions.SESSION_DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    finally:
        conn.close()

    assert {"session_id", "role", "content", "created_at"} <= columns
    assert "tool_calls" not in columns
    assert "tool_result" not in columns
