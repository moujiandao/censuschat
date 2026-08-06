# Reflection

## Development process and key architectural decisions

I worked this as a series of small, TDD-first, git-tracked slices mapped to
GitHub issues (`gh issue view <n>` for each issue's exit criteria and test
plan), grouped into milestones: M2 (tracer bullet — one question,
end-to-end, through the real stack), M3 (core agent behaviors: guardrail,
bounded recovery, ambiguity, watchdog, degraded mode), then this final
sprint, which cut hard into M4/M5 to ship a working, deployed demo instead.
I used Claude Code throughout — for implementation, for TDD (writing the
failing test first, confirming red, then implementing), and, distinctively,
for adversarial self-review: after nearly every feature, I dispatched a
separate code-reviewer subagent with no prior context on the change, asked
it to find real defects, and fixed what it found before moving on. That
pattern caught several things I'd have missed working alone under time
pressure — most notably a real security-adjacent bug (raw Snowflake
exception text able to leak into a client-facing message) and a genuine
architectural rule violation (a live per-request Snowflake connection that
contradicted this project's own "Snowflake touched only by `run_census_sql`
at request time" invariant) that I had to bring back to myself as a
judgment call rather than silently resolve.

A few decisions I'd call out as genuinely load-bearing:

- **Interpreting "conflicting" and "partially match" as concrete, testable
  cases rather than vague categories.** The dataset itself supplied the
  content: median-vs-mean aggregation (you cannot SUM a median across
  block groups; where a numerator/denominator pair exists, substitute a
  true mean and say so) and city/place questions (no city boundaries
  exist in this data, so a city query gets an honest redirect to its
  containing county, offered — never silently substituted). Both are
  logged as decisions (`docs/decisions.md` D-002, D-005) with the
  reasoning, not just implemented silently.
- **Code-enforcing the two invariants with an actual MUST in their
  contract** — bounded recovery (at most 2 retries after a SQL failure,
  then a deterministic, code-generated honest failure) and the ambiguity
  backstop (block `run_census_sql` outright, not just via a prompt
  instruction, if the model tries to proceed past an unresolved ambiguous
  geography). Both started as prompt-only and were tightened after I
  found, empirically, that a soft instruction isn't a boundary — the
  model doesn't always follow it, and "at most 2 retries" is a real
  promise about cost and latency, not a suggestion.
- **A boot-time-cached health check instead of a live one**, to avoid
  breaking the codebase's own Snowflake-access rule. This was the one
  point in the whole session where I stopped and asked rather than
  deciding alone — it was a real fork with a real cost either way
  (staleness vs. rule violation), not something I had standing to resolve
  unilaterally.

## What I'd improve or do differently with more time

The single most important thing I'd do differently: **build the frontend
first, or at least much earlier.** I built the entire backend — guardrail,
bounded recovery, ambiguity handling, the wall-clock watchdog, degraded
mode — fully tested and individually live-verified via raw HTTP calls,
before realizing with about three hours left that there was no web
interface at all, which is a hard, explicit requirement ("provide a
web-based interface accessible on the public internet"). The backend work
wasn't wasted, but the ordering was wrong: a reviewer can't evaluate a
`curl` command. Worse, building the frontend and testing it in a real
browser is what surfaced the single worst bug in the whole project — see
below. If I had built even a bare chat page in the first hour, I'd have
caught that bug during M2, not during the final sprint.

With more time I would, in priority order: (1) fix the state-level
mean-substitution query that currently exhausts the tool-loop iteration
cap (found in the manual eval run, `evals/README.md`) — this is a real,
reproducible failure on a legitimate question type; (2) wire
`normalize_value`'s top-code handling into the actual query-result
rendering path — it exists as a tested pure function but nothing calls it
yet, so a top-coded value ($250,000+ household income) can still reach a
real answer as a literal "$250,001" today; (3) build the automated eval
harness with the full 30-scenario golden set and an LLM-judge groundedness
check, so regressions get caught automatically instead of via ad hoc
manual runs; (4) add the decennial redistricting tables so "conflicting"
questions have a second, genuinely independent data source (right now
that requirement is covered only by the median/mean case, which is real
but narrower than the original design called for); (5) Langfuse tracing
for actual production observability — right now failures are only visible
via server logs and the SSE error text, not a queryable trace.

## Edge cases and failure modes identified but not fully addressed

- **The mean-substitution tool-loop-cap failure** (above) — found, not
  fixed. Likely cause: the model takes too many exploratory
  `search_census_variables` calls before settling on the right
  numerator/denominator pair for a state-wide aggregation, and hits the
  infra safety cap (`_MAX_TOOL_LOOP_ITERATIONS=8`) before finishing. A
  system-prompt hint toward the specific aggregate-pair pattern, or a
  slightly higher cap for this one tool, would likely fix it — untested.
- **Degraded-mode staleness.** Snowflake reachability is checked once, at
  boot, and cached (`src/health.py`, D-015) — a deliberate choice to keep
  the codebase's "Snowflake touched only by `run_census_sql` at request
  time" rule intact rather than violate it for a health check. The real
  cost: if Snowflake goes down mid-session without an app restart,
  `/api/health` keeps reporting healthy. I judged this acceptable because
  nothing currently polls `/api/health` continuously in production and a
  genuine outage still surfaces honestly the next time a real query fails
  (bounded recovery). If this app ever got a real uptime monitor, that
  judgment call should be revisited.
- **The guardrail occasionally over-refuses a genuinely unanswerable
  question** rather than letting it reach `resolve_geography` and return
  an honest zero-candidate "not found." In the manual eval run, "What's
  the population of Atlantis?" was refused as off-topic rather than
  reaching the tool loop. Defensible for a fictional place, but it means
  the "reasonable but unanswerable given the dataset" requirement is less
  thoroughly exercised than I'd like — I don't have a clean example that
  reliably tests the intended path instead.
- **No rate limiting, no per-session cost cap, no abuse protection beyond
  the guardrail and the SQL gate.** A public demo with only basic auth in
  front of it and no request throttling is a real production gap if this
  went beyond a take-home submission.
- **Single-instance session store** (SQLite, not Postgres/Redis) — fine
  for a demo, would not survive a second app replica without shared
  storage.

## Testing approach and what I'd add

TDD on everything deterministic: 287 tests, failing-test-first, covering
the SQL trust boundary (152 tests alone — this is the actual security
boundary in the system, so it got the deepest coverage), guardrail routing
logic, bounded-recovery counting, the ambiguity backstop's state machine,
the watchdog's wall-clock logic (a faked clock, not real sleeps), degraded-
mode detection, session replay, and `normalize_value`. Every one of these
is a case where a wrong answer is either a security hole or a silently
wrong number — worth the investment.

Deliberately *not* unit-tested: whether the model phrases a good answer,
picks the right tool, or handles a specific natural-language phrasing
well. That's LLM behavior, not deterministic code, and a mocked unit test
asserting on generated text is close to worthless — it tests the mock, not
the model. The intended mechanism for that is a golden-eval suite (30
named scenarios, an LLM-as-judge groundedness check) — scoped, partly
designed, and cut for time in this final sprint in favor of shipping a
working, deployed demo. In its place: individual live verification against
the real Anthropic + Snowflake backends after nearly every feature
(documented in `CHANGELOG.md` — this is also how the SQL-quoting bug and
the mean-substitution failure were actually found, not by the mocked
suite, which passed the whole time), plus one small manual eval run
(`evals/README.md`) covering the PRD's own named edge cases as a
lower-fidelity stand-in for the real harness.

If I were to add one thing to the test suite specifically: a repeatable,
automated version of the manual eval run — same scenarios, run on every
change, with results diffed against the previous run rather than eyeballed
once. That's the actual gap between what exists now and a harness that
would catch a regression like the mean-substitution failure automatically
instead of by chance during a final manual pass.

## What I deliberately left out, and why

Given roughly the last three hours before submission, I re-read the
assignment itself and found two unmet, non-negotiable requirements — no
web interface existed at all, and the live deploy hadn't been updated with
the day's work — against a backlog of smaller, real, but lower-leverage
features. I cut redistricting tables, Langfuse tracing, and the full
30-scenario automated eval harness with an LLM judge entirely, on the
reasoning that a complete backend nobody can actually try is a worse
submission than an honestly incomplete one with a working demo. The Evals
and Flow Diagram tabs I initially planned to cut too, but went back and
built once the core chat interface and critical fix were live — both are
real: the Evals tab renders the frozen `EvalRun` schema
(`src/contracts.py`) built from the manual scenario run rather than the
full harness (`evals/build_run_from_manual.py` reshapes it — an honest
stand-in, not the real thing), and the Flow Diagram tab renders each
turn's actual guardrail/tool-call trace client-side, reusing the SSE
events the chat UI already receives rather than a new backend data model.
Deciding what's genuinely cuttable versus just initially deprioritized
under a moving time estimate is itself the judgment call I'd want a
reviewer to notice: not every item on the original plan matters equally
under a real deadline, and the assignment says so directly — "incomplete
submissions that show strong judgment and self-awareness will score
better than complete submissions that lack them."
