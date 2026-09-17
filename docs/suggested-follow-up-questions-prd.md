# Suggested Follow-Up Questions

Status: approved for the narrower first slice in
`docs/suggested-follow-up-questions-plan.md`, now implemented on its feature
branch. Implementation proposals below are background; the plan records the
verified scope and evidence.

## Problem Statement

After receiving an answer, users need a useful next question. An appealing
suggestion is misleading if the dataset cannot answer it. Variable-name matches
alone do not establish compatible populations, valid aggregation, or usable data
for the counties being discussed.

## Solution

Start with one analytical action: **Compare renter share**. Show zero or one
button after an eligible county housing analysis. Its explanation reads:
"Compare the percentage of occupied homes that are rented in these same counties."

Code checks feasibility before offering it. Clicking executes that exact operation
with the checked counties and vintage, then displays a grounded answer in chat.

Use fixed wording and deterministic eligibility for this first slice. With one
operation, an LLM ranking call adds cost without a meaningful choice. After this
works, add another tested operation and let the LLM select at most two eligible
action IDs, or none. Arbitrary suggested questions are never executable actions.

Example starting question: "How many occupied homes are in Travis County, Texas,
and Harris County, Texas?" After a supported answer, offer the renter-share
comparison for that same pair.

Tradeoff: narrow coverage makes the entire suggestion-to-answer path verifiable;
open-ended generation would require a much broader feasibility checker.

## User Stories

1. As a user comparing housing in two counties, I want a relevant next question
   that the available data can answer.
2. As a user, I want one click to retain the exact counties and dataset vintage.
3. As a user, I want no suggestion when support is uncertain, and a clear message
   if an offered action later fails.

## Implementation Decisions

### One operation, one narrow trigger

The initial action is `compare_renter_share`, limited to exactly two distinct,
unambiguously resolved counties in the supported ACS vintage. Trigger it only
after a successful housing-count analysis, not after arbitrary demographic
questions. A completed renter-share action does not suggest itself again.

Capture context from this turn's discovery, geography resolution, executed SQL,
and complete QueryResults. The county pair and housing-count variables must be
traceable to the successful query that supports the answer. Merely resolving a
county earlier in the turn is insufficient. Recognize only the simple county
roll-up query shapes needed for this slice; if the relationship cannot be proved,
omit the button. Do not build a general SQL lineage engine or infer context from
answer prose. Context inherited only through earlier conversation text is outside
this first slice.

No suggestions on clarification, refusal, unsupported geography/vintage, empty or
truncated results, unrecovered errors, watchdog expiry, or tool-loop exhaustion.
An earlier successful query does not override a failed final outcome.

### Prove feasibility before rendering

1. Discover the renter-occupied count and total occupied-housing count through
   runtime variable search. Validate the exact field meanings, common occupied-
   housing universe, source, vintage, physical tables, and county aggregation
   support against one reviewed recipe. Ambiguous matches mean no suggestion.
2. Reuse the existing local snapshot. It already stores field breadcrumbs, but
   search currently returns the table title and universe without those details.
   Expose the existing breadcrumbs through the search result's description so
   code can verify field identity without changing its frozen field names.
   A match on "rent," a high search score, or `geo_levels` alone is insufficient.
3. Run one preflight query through `run_census_sql` and the existing SQL gate.
   Require both counties, non-truncated results, compatible block-group coverage,
   no missing/suppressed component values, positive denominators, and renter
   counts between zero and occupied totals. SQL aggregates must explicitly check
   coverage and missingness because `SUM` alone silently skips NULLs.
4. Compute each county's percentage as
   `100 * SUM(renter-occupied count) / SUM(occupied-housing count)`, returned in
   the QueryResult. Never average block-group percentages. An empty county,
   missing component, zero denominator, or invalid count suppresses the entire
   action rather than silently dropping one county.

The preflight has no retries and must fit within the existing turn deadline and
SQL timeouts. If insufficient time remains or it fails, return the original
answer without suggestions. Emit the usual tool events and trace spans for all
discovery and query calls. Do not expand the three-tool agent interface.

The support check proves observed data feasibility, not future service uptime.
The live data path still needs validation; this PRD does not assert that the
recipe has already passed against Snowflake.

### Execute the action, not its label

Return an optional `follow_ups` list in the existing `done` event's free-form
data, containing an opaque suggestion ID, action ID, label, and explanation.
No new SSE event enum is needed. Missing or empty lists render no controls.

Keep the validated recipe, discovered identifiers, county IDs, vintage, and
source-turn identity server-side. Use a bounded, short-lived in-process store,
with only the latest offer per session. Starting another turn invalidates it;
expiry or server restart makes it unavailable. Durable suggestion history and
multi-worker coordination are deferred.

Extend the chat request to accept either a message or a suggestion ID. Validate
that the suggestion belongs to the requesting session and latest eligible turn.
Never accept SQL, variable identifiers, or execution context from the button.
Use a small internal action handler without changing the frozen `agent_turn`
signature. Consume the offer atomically so duplicate clicks cannot run it twice.

The handler reruns the checked query through the same SQL gate and validates the
new results. This keeps every numeric claim grounded in this click's QueryResult,
as required by the existing turn-grounding invariant. Render a short templated
comparison identifying both counties, occupied-housing denominator, ACS period,
and block-group roll-up method. Do not claim statistical significance or causality.
No LLM must reinterpret the button, choose new variables, or write new SQL.

Persist the human-readable action request and answer through existing session
history. Preserve tracing, tool events, streaming, and terminal `done`/`error`
behavior. A stale offer or failed recheck yields a clear retry message, not a
silently substituted question or cached numeric answer.

### Keep integration small

Use a small follow-up helper for the recipe, eligibility, and execution context;
integrate it with the existing agent completion path, chat handler, and static
chat UI. Reuse discovery, SQL validation, normalization, sessions, and tracing.
No new dependencies, framework, tab, database schema, or build step.

Render the button below the latest completed answer. Disable it while a request
is running and remove it on use, a new question, or a new chat. Use a native
keyboard-accessible button. Existing Evidence controls remain interface actions,
not analytical recommendations.

These are proposed implementation decisions. Record the new data flow in the
project's decision log and changelog when implementing. Frozen contract changes,
if subsequently found necessary, require separately flagged approval.

## Testing Decisions

Test observable eligibility and execution behavior using the existing offline
tool, agent, API, and frontend test patterns. Write failing tests first for the
deterministic checker. Do not use mocked model responses as evidence of analytical
quality.

Acceptance checks:

- A supported two-county housing analysis offers exactly one renter-share action.
  Clicking preserves both counties and returns the expected ratio of sums from
  new query results. Unequal block-group denominators catch accidental averaging.
- Missing or ambiguous variables, incompatible universes/vintages, incomplete
  coverage, suppression, absent counties, zero denominators, and invalid counts
  all produce no offer.
- Unsupported or unrelated questions, already-answered renter share, uncertain
  context, and incomplete/failed turns produce no offer.
- Changing the display label cannot change execution. Unknown, altered, expired,
  cross-session, superseded, and duplicate suggestion IDs cannot execute it.
- Suggestion-generation failure preserves the original answer and terminal event;
  click-time failure reports honestly. Keyboard use and new-chat invalidation work.

Before calling the feature validated, run the full offline suite and a small
explicitly authorized live evaluation: two eligible county pairs from housing
questions, one unrelated question, and one ambiguous county question. For each
eligible case, click the offered action and independently check the returned
components and percentage. Brian reviews whether each suggestion advances the
analysis. Record offer eligibility, click completion, correctness, usefulness,
and added latency in the existing eval artifacts; keep and triage failures.

Pass means both eligible cases offer and complete correctly, both negative cases
offer nothing, and Brian finds the positive suggestions useful. Offline tests
alone do not establish that the live recipe works.

## Out of Scope

Additional analytical recipes; arbitrary metrics; new counties; city, ZIP, or
metro support; cross-vintage comparisons; significance testing; maps; new evidence
controls; LLM ranking or rephrasing; recommendation frameworks; persistent offers;
analytics dashboards; unrelated refactoring.

## Further Notes

Build one vertical slice: checked recipe, executable button, then live validation.
Only expand coverage after that path succeeds. The transferable principle is
**separate relevance from feasibility**: future LLM selection may narrow the
validated candidate set, never expand it.

### Handoff checkpoint

- Branch: `feat/follow-up-questions`, created from local `main` at `ddd82aa`.
- Separate worktree: `/Users/brianmar/workspace/censuschat-worktrees/follow-up-questions`.
- The approved first slice is implemented. The original checkout's unrelated
  edits are preserved. See the implementation plan and scoped live artifact for
  verification and known limits.
- Next step: Brian assesses usefulness before broadening the operation set.
  Merge and deployment require separate authorization.
- Codex-bridge is not available among this session's tools. This checkpoint is
  the local handoff record; no remote state persistence is claimed.
