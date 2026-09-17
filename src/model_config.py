"""Single source of truth for pinned model IDs (CLAUDE.md rule 14).

Sonnet remains the agent tool loop model (src/agent.py). GPT-5 nano handles
the guardrail classifier (src/guardrail.py; D-031). Model IDs still have one
home even though the two workloads now use different providers.
"""

from __future__ import annotations

AGENT_MODEL = "claude-sonnet-5"
CLASSIFIER_MODEL = "gpt-5-nano"
