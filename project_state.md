---
project: censuschat
priority: P2
state: BLOCKED
attention: DECISION
human_blocked: true
owner: brian
agent: codex
current_task: Preserve per-scenario evaluation traces
next_action: Add eval trace storage and display using Brian's chosen format
waiting_on: Brian's choice of committed eval JSON or local SQLite traces
updated: 2026-10-02T18:45:02Z
---

# Current State

Named live progress and durable trace labels are committed as `8413f60` on `feat/named-turn-progress`. Exposing per-scenario traces from Evals is pending an artifact decision.

# Recent Work

Full offline suite: 508 passed. JavaScript syntax passed. Browser preview exercised refusal, degraded mode, batched tools, and zero-row recovery. Read-only review passed after fixes.

# Blocker

Brian must choose whether per-scenario traces are embedded in committed EvalResult JSON (portable but changes the frozen contract) or read from the existing local SQLite trace store (no contract change, local to one machine).

# Next Action

Add eval trace storage and display using Brian's chosen format.

# Notes

The older dirty checkout remains untouched. Do not run the paid live eval without authorization.
