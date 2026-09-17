# Suggested Follow-Up Questions: implementation plan

Status: approved by Brian and implemented on `feat/follow-up-questions`, based
on `ddd82aa`. The PRD's broader implementation proposals were narrowed below.

## Recommendation and findings

Build one fixed Compare renter share action for two counties in the supported
ACS period. Narrow the trigger to a successful **occupied-housing total** query
in one supported county-aggregation shape. Unknown shapes get no suggestion.
Tradeoff: limited coverage avoids a general SQL-lineage or intent classifier.

- **Use agent context.** Full tool inputs/results are available inside
  `src/agent.py`; API events and traces contain summaries. `done` also terminates
  refusals and partial failures. Require a successful final model response and
  validated query context, excluding truncation, errors, ambiguity, watchdog,
  and loop exhaustion. Do not infer eligibility from answer prose.
- **Metadata needs one small repair.** The local snapshot contains total, owner,
  and renter Tenure fields in the occupied-housing universe. Search currently
  returns identical table labels and universe-only descriptions. Expose existing
  field detail through `description`, retaining universe information. Preserve
  ranking, frozen fields, and runtime identifier discovery. Keyword matches and
  the non-median `geo_levels` heuristic are insufficient.
- **Keep checks specific.** Use one reviewed recipe with components in the same
  physical housing table. Check missing/duplicate block groups and coverage
  against the same-vintage geographic reference. No general compatibility engine,
  LLM ranking, new tool/event type, dependency, or database migration.
- **Keep preflight and requery.** Preflight establishes data support; clicking
  must use fresh QueryResults under the grounding invariant. The 50-second
  watchdog is soft; SQL allows 25 seconds plus up to 10 seconds for connection.
  Measure latency before assuming preflight fits. Skipping an offer is acceptable;
  cancelling a Python thread does not establish SQL cancellation.

## Implementation sequence

1. **Prove the recipe.** With live-call authorization, verify runtime discovery,
   coverage, and latency for two county pairs. Capture actual starter-query
   shapes. Revise the slice if these checks fail before building its UI.
2. **Connect eligibility to execution.** Generate zero/one offer before the
   eligible turn finishes. Store one bounded, expiring offer per session, bound
   to the originating turn. Invalidate on new turns, reject late publication
   from older turns, and consume before awaiting execution. The current Docker
   command uses one worker; persistent/distributed offers are unnecessary.
3. **Complete the click flow.** Submit only the opaque offer ID. Requery through
   the SQL gate, validate, and stream a templated comparison. Preserve history,
   traces, tool events, and honest terminal errors. Verify through the browser.

| Affected file | Responsibility |
|---|---|
| `src/follow_ups.py` (new) | Single recipe, narrow context matcher, numeric checks, bounded offers, action stream. |
| `src/tools.py` | Expose existing field detail in search descriptions. |
| `src/agent.py` | Capture context; gate and preflight offers before final tracing/completion. |
| `src/app.py` | Backward-compatible message-or-offer request validation, invalidation, dispatch, SSE errors. |
| `static/index.html` | Native button, ID submission, busy state, removal on use/new question/new chat. |

Reuse contracts, SQL validation, sessions, and tracing without schema/signature
changes. Extract only small shared lifecycle helpers if needed. Record the new
flow in the existing decision log and changelog during implementation.

## Acceptance tests

- `tests/test_follow_ups.py` and `tests/test_tools.py`: exact field identity and
  ratio of sums with unequal denominators; reject ambiguity, incomplete/duplicate
  coverage, absent counties, NULLs, invalid counts, and zero county denominators.
  Zero-household block groups alone need not invalidate a county.
- `tests/test_agent.py`: one eligible completion offers once; unrelated,
  uncertain, truncated, refused, failed, or exhausted turns offer nothing.
  Preflight failure/insufficient budget preserves the original answer.
- `tests/test_app.py`: fresh gated execution retains counties/vintage/history and
  traces. Labels cannot change execution. Reject unknown, expired, cross-session,
  superseded, and duplicate IDs; late turns cannot resurrect an offer.
- Browser: clicking and keyboard activation complete; busy/new-chat behavior
  clears stale controls. Existing frontend tests inspect HTML text, so they
  cannot prove the interaction. Run the full offline suite before completion.
- Authorized live check: two eligible pairs, one unrelated question, one ambiguous
  county question. Independently verify components/percentages and record offer,
  click, latency, and Brian's usefulness assessment in existing eval artifacts.

## Evidence and approval

Brian approved the narrower trigger, additive chat API change, and bounded live
verification. No frozen-contract deviation was needed. Runtime metadata and live
coverage checks passed for Travis/Harris and Alameda/Contra Costa. Both starter
queries matched; mouse and keyboard activation produced fresh grounded answers.
Action queries took about 3 to 4 seconds. Unrelated and ambiguous questions
produced no offer. The scoped evidence is in
`evals/results/follow-ups/2026-09-17.json`; it is not a full golden eval run.
The full offline suite passed (498 tests, one existing Starlette/httpx warning).
The required code review passed after correcting argument formatting in Evidence.
Browser fixture checks verified busy controls and removal on new question/chat.

Handoff: implementation and automated checks complete; Brian's usefulness
assessment remains pending. The supported trigger is intentionally narrow,
offers disappear on restart, and one server process is assumed. Configuration
migration remains on its separate branch. Merge and deployment are not authorized.
