"""Agent loop. Placeholder for issue #7 (minimal agent loop, M2 tracer bullet).

`src/app.py` imports `agent_turn` from here rather than from `src/contracts.py`
directly, per contracts.py's own docstring: "Implementations live in sibling
modules; every function here raises NotImplementedError until its module
lands." This stub keeps that import boundary correct so app.py's streaming
tests can monkeypatch `agent_turn` without depending on the real Sonnet tool
loop, Snowflake, or the guardrail classifier.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.contracts import ChatEvent, EventType


async def agent_turn(session_id: str, user_message: str) -> AsyncIterator[ChatEvent]:
    """See src/contracts.py:agent_turn for the full contract. Not yet implemented."""
    raise NotImplementedError
    yield ChatEvent(type=EventType.DONE)  # pragma: no cover — async-gen marker
