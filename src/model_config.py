"""Single source of truth for pinned model IDs (CLAUDE.md rule 14).

Haiku handles the agent tool loop (src/agent.py; D-032). GPT-5 nano handles
the guardrail classifier (src/guardrail.py; D-031). Model IDs still have one
home even though the two workloads use different providers.
"""

from __future__ import annotations

AGENT_MODEL = "claude-haiku-4-5"
CLASSIFIER_MODEL = "gpt-5-nano"
