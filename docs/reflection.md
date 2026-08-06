# Reflection

Four sections, matching the assignment's deliverable list.

---

## 1. Development process and key architectural decisions

### Process

I spent the first block of the 24 hours not writing application code. In
order: schema recon against the live share (`docs/schema-notes.md`, a
73-object inventory produced by querying, not by reading marketing pages),
an architecture doc, a PRD containing a 30-scenario golden set, and a
frozen interface file (`src/contracts.py`). Only then did implementation
start, as small commits mapped to GitHub issues, each carrying its own exit
criteria and test list.

That ordering paid for itself twice. The recon found the traps that shaped
the whole design (block-group-only grain, no city geography, medians that
cannot be aggregated, an upstream-typo'd column name `FIELD_LEVELl_9`), and
the golden set was written before any agent existed, so the test cases were
not reverse-engineered from a system that already passed them. It also cost
me: I reached hour 20 with a fully tested backend and no web interface,
which is a hard requirement. More on that in section 2.

Implementation was TDD on every deterministic layer, per the project's own
rule 19: write the failing test, confirm red, implement. 322 tests today,
152 of them on `validate_sql` alone.

### How I used AI tooling

Claude Code wrote most of the code. The pattern worth describing is not
"generate then accept," it is adversarial self-review: after nearly every
feature, I dispatched a fresh code-reviewer subagent with no context on the
change and asked it to find real defects. It found things I would have
shipped:

- Raw Snowflake exception text (`str(exc)`, carrying driver and host
  detail) flowing into a client-facing recovery message and into the
  session store, contradicting this codebase's own documented rule that
  error events carry user-safe text only.
- A last-write-wins bug in the ambiguity backstop: an unrelated,
  unambiguous `resolve_geography` result silently cleared an earlier
  unresolved ambiguity, reopening the exact silent-pick path the backstop
  exists to close. Triggerable by any comparison question naming
  "Washington County" alongside a real county.
- A live per-request Snowflake connection in the health check that
  violated this project's own rule 13. That one I did not let the reviewer
  or the implementing agent resolve; it was a genuine architectural fork,
  so I made the call myself (D-015).

Where the reviewers were useless: none of them ever found the identifier
bug in section 4. They read code, and the code was correct. The defect was
in a prompt and only existed relative to a real database catalog.

### Five decisions I will defend

**1. The SQL gate is the trust boundary, and everything else is a soft
layer.** `validate_sql` parses with sqlglot in the Snowflake dialect,
requires a single SELECT, checks every table reference against a 31-table
allowlist with lexically scoped CTE resolution, injects and clamps `LIMIT`,
bans star projection, rejects zero-table statements, and rejects any
function call sqlglot cannot model. That last one is default-deny: a
denylist cannot enumerate UDFs or external functions, whose names are
arbitrary and which could ship rows to any endpoint. Measured cost of
default-deny: of 48 functions a census answer plausibly needs, 47 survive,
and only `RATIO_TO_REPORT` is rejected.

*Alternative considered:* prompt instructions plus a regex denylist. I
rejected it because three separate bypasses got past my own first
implementation and were only found by adversarially attacking the real
function (`SELECT a, SYSTEM$CANCEL_ALL_QUERIES() FROM <allowed>` passed
with zero violations; so did a decoy CTE named after a forbidden table). If
an AST-based gate written specifically for this had three holes, a regex
would have thirty.

*At production scale:* the gate stops being the only boundary. The real
control is a Snowflake role with SELECT on exactly those 31 objects and
nothing else, plus a resource monitor and a per-tenant warehouse, so a gate
bug is contained by the database rather than by my parser. The gate stays
as defense in depth and as the thing that produces good error messages, but
I would not want it load-bearing alone against untrusted traffic at volume.

**2. Variable discovery is retrieval, not prompt content.** The share has
8,164 field codes. None of them appear in any prompt. The agent finds
variables through `search_census_variables`, FTS5 over a local SQLite
snapshot of labels and metadata breadcrumbs, built at boot and never
touched by Snowflake at request time.

*Alternative considered:* a curated subset of "important" variables in the
system prompt. That is the version that demos well and fails the moment
someone asks about a topic I did not anticipate, and the assignment
explicitly warns against limiting yourself to a subset. The other
alternative was embeddings; I rejected that because these are short,
keyword-dense labels where BM25 with token-AND is strong, and because a
probe (`docs/schema-notes.md` Appendix A) showed OR-semantics ranking a
single-rare-term match above the true multi-term hit. Token-AND fixed it
without adding an embedding model to the boot path.

*At production scale:* BM25 over 3,782 rows in one vintage is fine. At
multiple vintages, multiple sources, and 10x the corpus, lexical retrieval
starts returning the right table number and the wrong row, and I would move
to hybrid retrieval with a reranker and evaluate retrieval separately from
the agent. The snapshot would become a versioned build artifact with its
own schema migration, not a file the app builds at startup.

**3. Invariants that use MUST language are enforced in code, not in the
prompt.** Three places: bounded recovery stops at 2 failed `run_census_sql`
attempts and emits a deterministic, code-generated honest failure; the
ambiguity backstop blocks Snowflake outright if the model tries to run SQL
while an ambiguous geography is unresolved; the 50s watchdog ends tool use
and builds a partial answer from rows already returned. None of these three
messages is model-generated, so the model cannot talk its way past its own
stop condition.

*Alternative considered:* all three as system-prompt instructions, which is
how each one started. I changed them after watching the failures. Bounded
recovery under the narrow reading (`SqlRejected` only) left genuine
Snowflake execution errors uncounted, so a model guessing bad SQL could
retry unbounded at full warehouse cost, which is exactly the exposure the
rule exists to prevent (D-013). "At most 2 retries" is a promise about
money and latency, and a promise a language model can decline is not a
promise.

*At production scale:* these become policy rather than constants. Per-tenant
budgets, a shared middleware that owns retry and deadline accounting across
all tools, and real cancellation instead of the current between-rounds
check. Today the watchdog cannot interrupt a model call already in flight,
which is an accepted soft bound at 50s against a 60s requirement; at scale
that headroom is not enough and you need per-call timeouts with proper task
cancellation.

**4. The median-aggregation trap is encoded as data, not as a sentence in a
prompt.** `VariableHit.geo_levels` was originally "where is this variable
available," which is a constant in this dataset (everything is block-group
grain and rolls up uniformly), so the field was dead. I reinterpreted it as
aggregation validity (C-3 in D-009): count variables carry all five levels,
the 28 median tables carry `[BLOCK_GROUP]` only. The retrieval layer must
populate it, so a test can assert on it.

*Alternative considered:* a line in the system prompt saying never average
medians. I rejected it because this is the most dangerous wrong answer the
system can produce: averaging block-group medians to a state median returns
a plausible number, with no error, no empty result, and nothing anywhere in
the response that looks wrong. Every other failure mode in this system is
loud. This one is silent, and silent wrong numbers are the failure a
data-grounded agent exists to prevent.

*At production scale:* this generalizes into a real semantic layer. Per
variable: valid aggregations, universe, the denominator to use for a rate,
whether a top-code applies. Maintained as data with its own test suite and
owned by whoever owns the dataset, not by whoever writes the prompt. The
one-off `"median" in table_title` heuristic I shipped happens to be 28/28
exact against the real catalog (D-011, verified), but a heuristic that
survives contact with one dataset is not a strategy.

**5. Two guardrail layers with opposite failure directions.** A Haiku
classifier runs before the agent loop and fails OPEN: any classifier error
or a 1.5s timeout returns ALLOW. The SQL gate fails CLOSED. The classifier's
job is a fast-fail path for off-topic and injection traffic (OT-01 refuses
in 1.4s with zero tool calls, INJ-02 in 3.5s), not safety.

*Alternative considered:* fail closed on classifier errors. That trades a
real, common failure (classifier outage blocks every legitimate question)
against a failure the classifier does not actually prevent, because the
gate is what stops dangerous SQL and the gate is unaffected by classifier
availability. The classifier's refusal text is also never shown to the user
or persisted, only a static canned message, which closes injection-via-
refusal-text.

*At production scale:* fail-open is only defensible while something else
bounds cost. Today nothing does: there is no rate limiting and no
per-session spend cap, so a classifier outage plus a scripted client is an
unbounded Anthropic and Snowflake bill behind one basic-auth password. At
scale the classifier stays fail-open and rate limiting plus per-tenant cost
caps become the thing that fails closed.

---

## 2. What I would improve or do differently with more time

### The ordering mistake

Build the interface first. I built the guardrail, bounded recovery,
ambiguity handling, the watchdog, and degraded mode, all tested and all
individually verified against live Snowflake through raw HTTP, before
noticing at roughly hour 20 that there was no web interface at all. The
backend work was not wasted, but a reviewer cannot evaluate a `curl`
command, and worse, the act of typing two questions into a browser found
the worst bug in the project in under a minute (section 4). A bare chat
page in hour 2 would have surfaced it in hour 2.

### What I cut, and what I got instead

**Langfuse tracing (rule 17) became an in-app Trace Logging tab.** What
shipped: `src/tracing.py` plus `GET /api/traces`, one span per guardrail
check, per model call (latency and input/output token counts read off the
Anthropic response's `usage`), and per tool call, for the last 20 turns per
session. It is genuinely useful, and it verifiably works: a live "population
of Montana?" turn showed token counts across 3 model calls and a
self-correcting `run_census_sql` retry. What it is not: it is in-process
and in-memory, so it dies on restart, is invisible across replicas, has no
persistent storage, no cross-session search, no aggregation, and no
alerting. It answers "what happened in this turn." Observability exists to
answer "what is happening across the deployed system," and this does not.
It is a debugging aid wearing an observability tab's name, and I would
rather label it that way than claim rule 17 is satisfied.

**The automated eval harness (issues #19 to #22) became 11 of 30
scenarios.** `make eval` does run for real: it drives `evals/scenarios.py`
against the real `agent_turn` with real Anthropic, real Snowflake, and the
real guardrail, accumulates tool evidence across all turns of a scenario,
scores six deterministic check types, and writes a committed `EvalRun`
(`evals/results/latest.json`) in the frozen contract schema, which the
Evals tab renders directly. The gaps are specific:

- `judge_groundedness` (#21) is unimplemented. It is scored as a loud
  failure rather than skipped, so it cannot inflate a pass rate, but the
  consequence is that **rule 2, the core grounding invariant, has no
  automated enforcement anywhere in this project.** Nothing checks that
  every number in an answer traces to a row this turn actually returned.
- The regression gate (#22) does not exist. Nothing runs `make eval`
  automatically, so nothing catches a regression between commits.
- 19 of the PRD's 30 scenarios are not implemented, and a further 25
  scenarios I authored afterward (covering malformed input, injection
  shapes beyond one, NULL and top-coded values, multi-turn drift, and a
  worst-case latency check against the 60s bound) are marked
  `status="pending"`: authored, never executed, excluded from the
  denominator. They are a specification of intended behavior, not evidence
  of it, and they carry a bias risk the PRD rows do not, because I wrote
  them after seeing the system work.

**`normalize_value` exists, is tested, and is wired to nothing.** This is
the cut I am least comfortable with, because it is a live wrong-number
path. The function correctly returns `top_coded=True` when a variable's
table number matches `TOP_CODES`. Nothing calls it. Concretely: 776 block
groups in this share report `B19013e1 = 250001`, which is the Census
"$250,000 or more" top-code, not a median income of $250,001. Ask for
median household income in one of those block groups today and the value
goes from Snowflake through `run_census_sql`'s row serialization into the
model's context as the bare integer 250001, and the model has no way to
know it is a band. The most likely rendering is "$250,001", stated as a
precise figure. That is not a hallucination, the number came from the
database, and it is still wrong in the way that matters. The fix is small
(call `normalize_value` in row serialization and narrate the band); I ran
out of hours before doing it.

**Decennial redistricting tables (D-004) cut, so the `conflicting`
category has zero coverage.** The assignment asks for conflicting
questions. My design answered that with a genuine second source: the
full-count decennial tables, so "population of Travis County" has two
defensible answers the agent must surface and explain. Without them, the
conflict requirement is covered only by the median-versus-mean case, which
is real but narrower than designed.

**The two known-red scenarios are still red, and the current 11/11 does not
show them.** This is worth stating plainly because a 100% pass rate is
exactly the kind of number that should be distrusted:

- `PM-01`, "What's the average household income in Texas?", exhausts the
  8-iteration tool-loop cap. In the recorded run the model made 11 tool
  calls, 9 of them `search_census_variables`, hunting for the
  numerator/denominator pair for a state-level true mean, and terminated
  with "[Stopped after reaching this turn's tool-call limit.]" after
  33.8 seconds. It is a legitimate question and a real reproducible
  failure.
- The Atlantis case: "What's the population of Atlantis?" is refused by the
  guardrail as off-topic rather than reaching `resolve_geography` and
  returning an honest zero-candidate "not found." Defensible for a
  fictional place, but it means the "reasonable but unanswerable" path is
  exercised less thoroughly than I would like, and I do not have a clean
  scenario that reliably tests the intended route.

Neither is in the current golden subset, so `make eval` is green with both
failures live in the system. The pass rate measures the checks I wrote, not
the system.

### Ranked, if I had another day

1. Wire `normalize_value` (live wrong-number path).
2. Implement `judge_groundedness` (rule 2 currently has zero automated
   enforcement).
3. Fix PM-01, likely a prompt hint toward the aggregate-pair pattern plus a
   higher cap for `search_census_variables` specifically.
4. Execute the 25 pending scenarios and triage whatever goes red.
5. Rate limiting and a per-session cost cap.
6. Decennial tables, for a real `conflicting` case.
7. Real Langfuse.

---

## 3. Edge cases and failure modes identified but not fully addressed

- **PM-01 tool-loop exhaustion.** Above. Found, reproduced, not fixed.
- **Top-coded values reaching the user as precise figures.** Above. The
  guard is written and unwired.
- **Guardrail over-refusal of fictional or absent geography.** Above. The
  fast-fail path and the honest-not-found path compete, and today fast-fail
  wins for cases where not-found would be the better answer.
- **Degraded-mode staleness.** Snowflake reachability is checked once at
  boot and cached (D-015), deliberately, to avoid violating this project's
  own rule that Snowflake is touched at request time only by
  `run_census_sql`. Cost: if Snowflake dies mid-session without a restart,
  `/api/health` keeps reporting healthy. Acceptable today because nothing
  polls it continuously and a real outage still surfaces honestly through
  bounded recovery on the next query. If this ever gets an uptime monitor,
  the decision should be revisited with a periodic background refresh.
- **The watchdog is soft.** It checks the wall clock between tool rounds
  and never aborts a call in flight, so a single pathological model call
  can overrun the 50s budget. Against the 60s requirement that leaves 10s
  of slack, and MT-01 already runs 38.9s in a normal two-turn case. The
  right fix is per-call timeouts and cancellation, not a bigger budget.
- **No rate limiting, no per-session cost cap, no abuse protection beyond
  the guardrail and the SQL gate.** A public endpoint that calls Anthropic
  and Snowflake per request, behind one shared basic-auth password, is an
  unbounded cost exposure. Fine for a demo with a known reviewer list, not
  fine for anything else.
- **Single-instance state.** Sessions are SQLite on a local volume and
  traces are in process memory. A second replica would split both. This is
  a deliberate demo-scoped choice, but it means the deployment cannot scale
  horizontally at all, not merely that it scales badly.
- **US territories are excluded from geography.** 13 county-grain rows
  (American Samoa, Guam, Northern Mariana Islands, US Virgin Islands) have
  a NULL `STATE` in the source and were dropped from the snapshot rather
  than given an invented display-name policy (D-012). Asking about Guam
  returns zero candidates. The failure is honest but the scope gap is
  invisible to the user.
- **Cross-vintage questions can only ever get an explanation.**
  `ALLOWED_TABLES` is 2020 only (D-003), because block groups were redrawn
  for 2020 and the 5-year windows overlap 4 of 5 years, so a comparison
  against 2019 is statistically invalid. Enforcing that at the gate makes
  the invalid query impossible rather than discouraged, which I stand by,
  but "how did this change since 2019" is a reasonable question this system
  structurally cannot answer.
- **Weak checks in the eval set.** `PM-02`'s check asserts only that the
  answer contains the word "median" when what the PRD actually wants is an
  explanation of why medians cannot be aggregated. The live answer did
  explain it correctly. The check would not have noticed if it had not.

---

## 4. Testing approach and what I would add

### What is tested, and what deliberately is not

322 tests, TDD-first, on every layer where a wrong answer is either a
security hole or a silently wrong number: the SQL gate (152 tests on its
own), guardrail routing and both fail-open paths, bounded-recovery
counting, the ambiguity backstop's state machine, the watchdog against a
faked clock rather than real sleeps, degraded-mode detection, session
replay ordering, `normalize_value`, and the eval scorer itself (a scorer
bug silently invalidates every result, so it got production-grade
treatment, including a test pinning that `GEO_RESOLVED` keys off tool
evidence rather than prose, so a model that merely names an ID without
resolving it fails).

Deliberately not unit-tested: whether the model phrases a good answer,
picks the right tool, or handles a particular phrasing. That is model
behavior, and a mocked assertion on generated text tests the mock. Golden
evals are the right instrument for it, which is why the golden set was
designed in the PRD before any agent code existed.

That split is correct in principle. What follows is the case where it
failed completely.

### The unquoted-identifier incident

**The state before.** 280 tests green. Every layer individually verified
against live Snowflake as it was built: `run_census_sql` had executed real
queries against real allowlisted tables, DF-01 (Alameda County) had
returned a correct grounded 1,661,584 end to end, bounded recovery had been
live-verified twice. By any measure I had available, the system worked.

**What happened.** I built the chat page, opened it in a browser, and typed
the first two questions a reviewer would type. "What's the population of
Wyoming?" failed. "What about Travis County, Texas?" failed. Both burned
the full 2-attempt recovery budget and returned the honest failure message.
Every single `run_census_sql` call in the system was failing with
`invalid identifier`.

**Root cause.** SafeGraph stores ACS variable columns case-sensitively
(`B01001e23`), confirmed by querying `INFORMATION_SCHEMA.COLUMNS` rather
than assumed. Snowflake folds an unquoted identifier to uppercase, so
`SUM(B01003e1)` resolves to `B01003E1`, which does not exist. The
`SYSTEM_PROMPT`'s own aggregation-pattern example showed an unquoted
`SUM(<variable_id>)`. The prompt was teaching the model the one pattern
guaranteed to fail against this database.

**Why the mocked suite structurally could not catch it.** Three reasons,
and they compound:

1. *The gate is a safety boundary, not a correctness boundary.*
   `validate_sql` passed the query, correctly. It is a single SELECT
   statement, it parses cleanly in the Snowflake dialect, it references an
   allowlisted table, `LIMIT` is injected. All 152 gate tests are about
   what the query is *allowed* to do. None of them can know whether a
   column identifier resolves against a real catalog, and none of them
   should: that is not what a trust boundary is for.
2. *The fake connection encoded the same assumption the code did.* Tests
   that exercised `run_census_sql` stubbed the Snowflake connector, and a
   stub returns rows for any syntactically valid SQL. This is the same
   class of failure as D-012, where two real bugs (`STATE` is a postal
   abbreviation, not a full name; 13 territory rows have NULL `STATE`) were
   invisible to a 224-test suite because the fixtures were written from the
   same wrong belief as the implementation. A mock written by the author of
   the code cannot disagree with the author of the code.
3. *The defect lived in a prompt, and its consequence lived in Snowflake's
   identifier resolution.* There is no artifact in between for a unit test
   to hold. To catch this you need a real column name from a real database,
   which is precisely what the mocked suite is designed to avoid needing.

Adding more mocked tests would not have helped at any point. The suite is
322 tests today, and none of the 42 added since would catch it either.

**The part I find most instructive: graceful degradation hid it.** This
defect had already surfaced twice before the browser found it, and both
times it was recorded as a success. During the M2 tracer bullet, the model
hit the case-sensitivity error and self-corrected to a quoted identifier on
retry inside the tool loop, and I logged that as the agent handling a
Snowflake quirk. During D-013's live verification, the same thing happened
again and I logged it as evidence that a successful retry is not penalized.
Both entries are true. Both describe a systematic prompt defect as normal
agent resilience. It only became visible when two questions in a row failed
to self-correct within budget, which is also, not coincidentally,
immediately after D-013 correctly tightened the budget. The lesson is
uncomfortable and general: **a good recovery path raises the cost of
noticing the thing it is recovering from.** Every retry that succeeds is a
failure that does not get investigated. If I were doing this again I would
instrument recovery attempts as a first-class metric with a threshold,
because "the agent self-corrected" is a signal, not a non-event.

**The fix.** An explicit highest-priority correctness rule in the system
prompt ("every `variable_id` column reference MUST be double-quoted") plus
quoting the aggregation example itself. Verified live immediately after:
Wyoming 581,348, Travis County 1,250,884, and a follow-up question
resolving correctly through session context.

**What the cut harness would have caught, precisely.** `make eval` scores
`DF-05` with `ANSWER_CONTAINS "581,348"` and `GEO_RESOLVED "56"` from tool
evidence, and `CMP-01` with `GEO_RESOLVED` on both Travis and Fulton, all
against the real stack. Under this bug, both scenarios end in the
honest-failure message containing no number and no successful SQL, so both
fail, loudly, naming the scenario. The full 11-scenario run takes about
four minutes of wall clock (individual scenarios run 1.4s to 38.9s). The
scenario design existed from hour 1. Building the runner cost roughly an
hour when I finally did it. I cut it for time and paid for that cut with
roughly 18 hours of a completely broken system that every test I had said
was fine.

That is the whole argument for eval-first on an LLM system, in one
incident: **the mocked suite tests my code, and the eval harness tests the
system.** They fail differently, and only one of them was capable of
noticing that the product did not work.

### What I would add, in order

1. **`judge_groundedness` (#21).** Rule 2 says every numeric claim must
   come from this turn's returned rows, and today nothing enforces it
   automatically. This is the single largest hole in the test strategy: the
   project's most important invariant is checked by reading answers.
2. **A live smoke test in CI on any commit touching `SYSTEM_PROMPT`,
   `src/tools.py`, or `src/sqlgate.py`.** One real question, real
   Snowflake, assert a known number. About 20 seconds and a few cents per
   run, and it catches this exact class of defect on the commit that
   introduces it.
3. **A contract test against the real catalog.** Assert from
   `INFORMATION_SCHEMA.COLUMNS` that variable columns are mixed-case, so
   the assumption that broke everything is pinned by data rather than by a
   sentence in a prompt. Same treatment for the other schema beliefs that
   already bit me once: `STATE` holding a postal abbreviation, NULL states
   in territory rows.
4. **Execute the 25 pending scenarios and triage the reds.** They cover
   malformed input, more injection shapes, NULL and top-coded values,
   multi-turn drift, and worst-case latency against the 60s bound. Right
   now they are a claim, not a result.
5. **The regression gate (#22),** so a pass-rate drop fails a commit rather
   than waiting for someone to look at a tab.
6. **Strengthen the weak checks.** `PM-02` asserting the word "median"
   would pass an answer that says "median" while doing the wrong thing. The
   general fix is to test the checks: mutate a known-good answer into a
   known-bad one and confirm the check goes red. A check that has never
   failed has not been tested.
