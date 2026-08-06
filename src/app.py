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
from src.tracing import get_traces

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


def _scenario_index() -> dict[str, dict]:
    """Scenario id -> the authoring context a stored EvalResult doesn't carry.

    An EvalResult records what happened (checks, answer, elapsed) but not what
    was *asked* or why, so the Evals tab could only ever render opaque ids.
    Rather than widen the frozen contract (CLAUDE.md rule 12) — which would
    also only help runs recorded from now on — the question text, notes, and
    provenance are joined on at read time from evals/scenarios.py, which
    retroactively labels the result files already committed.

    Imported lazily and defensively: a deployment without evals/ on the path
    must still serve stored results, just unlabeled.
    """
    try:
        from evals.scenarios import GOLDEN_SCENARIOS, PRD_SCENARIO_IDS
    except Exception:  # pragma: no cover - defensive; evals/ is shipped
        logger.warning("evals.scenarios unavailable; serving unlabeled results")
        return {}

    return {
        s.id: {
            "turns": list(s.turns),
            "notes": s.notes,
            # PRD §7 rows were designed before any agent code existed;
            # everything else was authored against a working system. The
            # distinction is what a reader needs to weigh a green row.
            "provenance": "prd" if s.id in PRD_SCENARIO_IDS else "authored",
        }
        for s in GOLDEN_SCENARIOS
    }


def _annotate(run: dict | None, index: dict[str, dict]) -> dict | None:
    """Attach scenario context to each result, in place on the loaded dict.

    A result whose scenario id no longer exists in scenarios.py (an older run
    referencing a since-renamed row) is annotated as "unknown" rather than
    dropped or raised on — a stale id is a labeling gap, not a reason to fail
    the whole tab.
    """
    if not run:
        return run
    for result in run.get("results", []):
        meta = index.get(result.get("scenario_id"))
        result["turns"] = meta["turns"] if meta else []
        result["notes"] = meta["notes"] if meta else None
        result["provenance"] = meta["provenance"] if meta else "unknown"
    return run


def _load_evals() -> dict:
    """The Evals tab renders latest vs. previous EvalRun directly (per
    docs/01-architecture.md) — evals/results/latest.json is always the
    most recent run; the second-most-recent timestamped file (everything
    in the directory except latest.json itself) is "previous", or None on
    a repo with only one run on record.

    Each result is enriched with its scenario's turns/notes/provenance —
    see _scenario_index."""
    latest_path = _EVALS_RESULTS_DIR / "latest.json"
    if not latest_path.exists():
        return {"latest": None, "previous": None}

    latest = json.loads(latest_path.read_text())
    timestamped = sorted(
        p for p in _EVALS_RESULTS_DIR.glob("*.json") if p.name != "latest.json"
    )
    previous = json.loads(timestamped[-2].read_text()) if len(timestamped) >= 2 else None

    index = _scenario_index()
    return {"latest": _annotate(latest, index), "previous": _annotate(previous, index)}


@app.get("/api/evals")
async def evals() -> dict:
    # File I/O only (no Snowflake) — off the event loop for consistency
    # with every other blocking call in this app, even though it's cheap.
    return await asyncio.to_thread(_load_evals)


@app.get("/api/traces")
async def traces(session_id: str) -> dict:
    """Trace Logging tab (src/tracing.py, a lightweight stand-in for the
    full Langfuse integration in issue #18 — see docs/reflection.md).
    In-memory, non-blocking read — no asyncio.to_thread needed, unlike
    /api/health and /api/evals, since this never touches the filesystem
    or Snowflake."""
    return {"traces": [json.loads(t.model_dump_json()) for t in get_traces(session_id)]}


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
