# Contextual Next Questions: implementation plan

Status: approved by Brian on 2026-09-16. Implementation starts from `main` at
`b692ecb` and replaces the narrower checked renter-share action described by
D-029.

## Design

Return zero to three deterministic next questions in the existing terminal
`done` event. Selection uses evidence already observed during the completed
turn: successful variable discovery, unambiguous resolved counties, and a
successful query. It does not inspect answer prose, call another model, or run a
speculative Snowflake query.

The initial catalogue contains three distinct directions for an eligible
two-county comparison:

1. Compare renter share when the turn discovered the compatible occupied-total
   and renter components.
2. Compare the same metric with another county.
3. Compare age distributions when the turn discovered multiple compatible age
   fields from the same table.

Templates contain the resolved county names so they retain context when edited.
Unknown query shapes, ambiguous geography, failed tools, incomplete completion,
and unsupported metric families produce fewer questions or none. Eligibility is
intentionally conservative.

The browser renders plain-text native buttons below only the latest completed
answer. A click fills and focuses the existing input without making a request.
If the input already contains a draft, the first click asks for explicit
replacement through a small inline confirmation. Starting a request or a new
chat removes all prior suggestions. A monotonically increasing browser turn ID
prevents a delayed terminal event from restoring stale controls.

## Replacement boundary

Remove the old preflight query, in-memory opaque offers, `suggestion_id` request
variant, and direct action stream. Keep the optional `follow_ups` field on the
terminal event to minimize the public contract change, but replace each entry
with a stable `id` and human-editable `question`. Normal submission continues to
send `{session_id, message}` through the existing guarded agent path.

Tradeoff: this loses the old hard-coded renter-share execution guarantee. It
gains editable exploration, supports multiple directions, removes hidden query
latency, and keeps every submitted question on the same observable agent path.

## Files and verification

- `src/follow_ups.py`: deterministic context capture and catalogue selection.
- `src/agent.py`: publish questions only after a successful eligible turn.
- `src/app.py`: restore the single-message chat request contract.
- `static/index.html`: render, protect drafts, and reject stale UI updates.
- `tests/test_follow_ups.py`, `tests/test_agent.py`, and frontend contract tests:
  cover zero-to-three selection, suppression, metadata resilience, and UI
  behavior.
- `docs/decisions.md`, `CHANGELOG.md`, and reviewer documentation: record the
  replacement of D-029.

Write failing deterministic tests first, run focused tests during development,
then run the full offline suite and the required code review. Live Snowflake
scenarios and deployment require separate authorization.

## Handoff checkpoint

Implementation is complete on `feat/contextual-next-questions` in the worktree
`/Users/brianmar/workspace/censuschat-worktrees/contextual-next-questions`.
The full offline suite passes with 482 tests and one existing dependency warning;
the required code-review gate passed after tightening template prerequisites and
county-query evidence. Frontend JavaScript syntax also passed direct parsing.
The original checkout and its untracked artifacts are untouched. Live Snowflake
scenarios, merge, and deployment remain separate actions.
