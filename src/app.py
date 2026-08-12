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
from src.tracing import get_traces, list_recent_sessions

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
    """Scenario id -> the question and notes a stored EvalResult doesn't carry.

    An EvalResult records what happened (checks, answer, elapsed) but not what
    was *asked* or why, so the Evals tab could only ever render opaque ids.
    Rather than widen the frozen contract (CLAUDE.md rule 12) — which would
    also only help runs recorded from now on — both are joined on at read time
    from evals/scenarios.py, which labels the result files already committed.

    Imported lazily and defensively: a deployment without evals/ on the path
    must still serve stored results, just unlabeled.
    """
    try:
        from evals.scenarios import GOLDEN_SCENARIOS
    except Exception:  # pragma: no cover - defensive; evals/ is shipped
        logger.warning("evals.scenarios unavailable; serving unlabeled results")
        return {}

    return {
        s.id: {
            "turns": list(s.turns),
            "notes": s.notes,
            "suite": s.suite.value,
        }
        for s in GOLDEN_SCENARIOS
    }


def _annotate(run: dict | None, index: dict[str, dict]) -> dict | None:
    """Attach the question and notes to each result, in place on the loaded dict.

    Rows an older harness recorded as "pending" are dropped: they were
    scenarios authored but never run, a distinction the set no longer carries,
    and an unrun scenario is not evidence about anything. They stay in the
    stored files, which are a historical record and shouldn't be rewritten to
    tidy the UI.

    A row whose scenario is no longer in scenarios.py is kept, unlabeled. It
    still records a real run, and dropping it would empty the tab entirely on
    the import-failure path below.
    """
    if not run:
        return run
    kept = []
    for result in run.get("results", []):
        if result.get("status") == "pending":
            continue
        meta = index.get(result.get("scenario_id"))
        result["turns"] = meta["turns"] if meta else []
        result["notes"] = meta["notes"] if meta else None
        result["suite"] = result.get("suite") or (
            meta["suite"] if meta else "capability"
        )
        result["outcome"] = result.get("outcome") or (
            "pass" if result.get("passed") else "fail"
        )
        kept.append(result)
    run["results"] = kept
    return run


def _history() -> list[dict]:
    """Every recorded run, oldest first, as a compact pass/fail projection.

    Deliberately not the full runs: the tab only needs which scenario passed
    in which run to draw the matrix, and shipping every answer and check for
    every historical run would grow the payload without being read.

    `latest.json` is skipped because it is a copy of the newest timestamped
    file, and counting it twice would show a phantom extra run — which, on a
    view whose whole job is "are we improving," is the worst kind of bug.
    """
    runs = []
    for path in sorted(_EVALS_RESULTS_DIR.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            run = json.loads(path.read_text())
        except Exception:  # pragma: no cover - a corrupt file shouldn't kill the tab
            logger.warning("skipping unreadable eval result %s", path.name)
            continue
        runs.append(
            {
                "run_at": run.get("run_at"),
                "git_sha": run.get("git_sha"),
                "pass_rate": run.get("pass_rate"),
                "scenarios": {
                    r["scenario_id"]: r.get("outcome")
                    or ("pass" if r.get("passed") else "fail")
                    for r in run.get("results", [])
                    if r.get("status") != "pending"
                },
            }
        )
    return runs


def _load_evals() -> dict:
    """What the Evals tab renders: the newest run in full, plus a compact
    history of every recorded run so the tab can show whether things are
    improving.

    `evals/results/latest.json` is always a copy of the most recent run. Each
    result in it is enriched with its scenario's question and notes — see
    _scenario_index.
    """
    latest_path = _EVALS_RESULTS_DIR / "latest.json"
    if not latest_path.exists():
        return {"latest": None, "history": []}

    latest = json.loads(latest_path.read_text())
    return {"latest": _annotate(latest, _scenario_index()), "history": _history()}


@app.get("/api/evals")
async def evals() -> dict:
    # File I/O only (no Snowflake) — off the event loop for consistency
    # with every other blocking call in this app, even though it's cheap.
    return await asyncio.to_thread(_load_evals)


@app.get("/api/traces")
async def traces(session_id: str) -> dict:
    """Evidence traces from the local SQLite store (src/tracing.py), not a
    Langfuse integration.

    Runs on a worker thread: since D-023 this reads SQLite on the mounted
    volume rather than a process dict, so it touches the filesystem and must
    not block the event loop — same treatment as /api/health and /api/evals.
    """
    records = await asyncio.to_thread(get_traces, session_id)
    return {"traces": [json.loads(t.model_dump_json()) for t in records]}


@app.get("/api/trace-sessions")
async def trace_sessions() -> dict:
    """Every session with recorded history, newest first.

    This is what makes history from *previous visits* reachable. `session_id`
    lives in the browser's localStorage, so without this endpoint a reviewer
    who opens a private window — or a different machine — can see only the
    session they are currently in, even though the traces are all durably
    on disk (D-023).
    """
    return {"sessions": await asyncio.to_thread(list_recent_sessions)}


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
