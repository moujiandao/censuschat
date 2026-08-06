"""FastAPI app. POST /api/chat streams ChatEvents as SSE (PRD §2, issue #6).

CLAUDE.md rule 11: every turn streams ChatEvents, every stream terminates
with DONE or ERROR, no hangs, no unhandled exceptions reaching the client.
Because streaming has already started by the time an error can occur, a
mid-turn exception cannot become an HTTP 500 — it becomes an ERROR event on
the open stream instead.

Startup builds the local snapshot (issue #2) and checks Snowflake
reachability (issue #15) via a lifespan handler; a SnapshotError there must
never crash the app (SnapshotError's own docstring in src/contracts.py) —
it boots degraded instead, reported by /api/health (src/health.py).
Snowflake reachability is checked exactly this once — CLAUDE.md rule 13
means neither /api/health nor a chat turn's degraded check may probe it
again at request time; both read this boot-time result from src/health.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.agent import agent_turn
from src.contracts import ChatEvent, EventType, SnapshotError
from src.health import check_snowflake_reachability, health_report
from src.snapshot import build_snapshot

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await asyncio.to_thread(build_snapshot)
    except SnapshotError:
        logger.warning(
            "Snapshot build failed at startup; booting in degraded mode", exc_info=True
        )
    await asyncio.to_thread(check_snowflake_reachability)
    yield


app = FastAPI(lifespan=lifespan)

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


@app.get("/")
async def index() -> FileResponse:
    # One static HTML file, vanilla JS, no build step (CLAUDE.md rule 15).
    return FileResponse("static/index.html")


@app.get("/api/health")
async def health() -> dict:
    # health_report() does blocking I/O (a file stat, plus reading the
    # boot-time-cached Snowflake result) — off the event loop, same as
    # agent_turn's tool calls and session-store I/O.
    return await asyncio.to_thread(health_report)


_EVALS_RESULTS_DIR = Path("evals/results")


def _load_evals() -> dict:
    """The Evals tab renders latest vs. previous EvalRun directly (per
    docs/01-architecture.md) — evals/results/latest.json is always the
    most recent run; the second-most-recent timestamped file (everything
    in the directory except latest.json itself) is "previous", or None on
    a repo with only one run on record."""
    latest_path = _EVALS_RESULTS_DIR / "latest.json"
    if not latest_path.exists():
        return {"latest": None, "previous": None}

    latest = json.loads(latest_path.read_text())
    timestamped = sorted(
        p for p in _EVALS_RESULTS_DIR.glob("*.json") if p.name != "latest.json"
    )
    previous = json.loads(timestamped[-2].read_text()) if len(timestamped) >= 2 else None
    return {"latest": latest, "previous": previous}


@app.get("/api/evals")
async def evals() -> dict:
    # File I/O only (no Snowflake) — off the event loop for consistency
    # with every other blocking call in this app, even though it's cheap.
    return await asyncio.to_thread(_load_evals)


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
