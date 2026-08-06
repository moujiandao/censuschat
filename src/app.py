"""FastAPI app. POST /api/chat streams ChatEvents as SSE (PRD §2, issue #6).

CLAUDE.md rule 11: every turn streams ChatEvents, every stream terminates
with DONE or ERROR, no hangs, no unhandled exceptions reaching the client.
Because streaming has already started by the time an error can occur, a
mid-turn exception cannot become an HTTP 500 — it becomes an ERROR event on
the open stream instead.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.agent import agent_turn
from src.contracts import ChatEvent, EventType

logger = logging.getLogger(__name__)

app = FastAPI()

_TERMINAL_EVENTS = {EventType.DONE, EventType.ERROR}

# EventType.ERROR's contract (src/contracts.py) requires "user-safe text
# only" — never the exception's own message, which may leak internals.
_GENERIC_ERROR_MESSAGE = "An internal error occurred while processing your request."


class ChatRequest(BaseModel):
    session_id: str
    message: str


def _encode_event(event: ChatEvent) -> bytes:
    payload = {"type": event.type.value, "data": event.data}
    return f"data: {json.dumps(payload)}\n\n".encode()


def _safe_for_log(value: str) -> str:
    """session_id is client-supplied and unvalidated. A raw newline in it
    would let a client forge fake log lines; escape control characters
    before it ever reaches a log call."""
    return value.replace("\r", "\\r").replace("\n", "\\n")


async def _stream_turn(session_id: str, message: str):
    try:
        async for event in agent_turn(session_id, message):
            yield _encode_event(event)
            if event.type in _TERMINAL_EVENTS:
                return
    except Exception:
        logger.exception(
            "agent_turn failed for session_id=%s", _safe_for_log(session_id)
        )
        yield _encode_event(
            ChatEvent(type=EventType.ERROR, data={"message": _GENERIC_ERROR_MESSAGE})
        )
        return

    # agent_turn's own contract requires it to always end in DONE or ERROR.
    # This is the backstop for a violation of that contract, not the
    # expected path — the stream must still terminate honestly either way.
    logger.error(
        "agent_turn for session_id=%s ended without a terminal event",
        _safe_for_log(session_id),
    )
    yield _encode_event(
        ChatEvent(type=EventType.ERROR, data={"message": _GENERIC_ERROR_MESSAGE})
    )


@app.get("/api/health")
async def health() -> dict:
    # Minimal liveness check for the deploy script. Issue #15 (degraded mode)
    # enriches this with snapshot/Snowflake reachability status.
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_turn(req.session_id, req.message),
        media_type="text/event-stream",
        headers={
            # Defeats buffering on Caddy and, per PRD §12, on Cloudflare —
            # verified end-to-end in issue #10.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
