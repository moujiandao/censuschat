"""Single source of truth for pinned model IDs (CLAUDE.md rule 14).

Sonnet for the agent tool loop (src/agent.py). Haiku for the guardrail
classifier (src/guardrail.py, issue #11 — not yet implemented; pinned here
ahead of that module landing so there is only ever one place to change it).
"""

from __future__ import annotations

AGENT_MODEL = "claude-sonnet-5"
CLASSIFIER_MODEL = "claude-haiku-4-5"
