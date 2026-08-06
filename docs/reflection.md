# Reflection

Four sections, matching the assignment's deliverables. Written afterwards, so
the mistakes are in here too.

---

## 1. Process and the decisions that mattered

### How I worked

I spent the first several hours not writing application code. First I queried
the actual Snowflake share and wrote down what was really in it
(`docs/schema-notes.md`). Then an architecture doc, a PRD with a 30-scenario
golden set, and a frozen interface file (`src/contracts.py`). Implementation
came after that, as small commits tied to GitHub issues.

That ordering was mostly right. The recon caught the traps that shaped
everything downstream: the data is block-group grain only, there are no city
boundaries, medians cannot be summed, and one column has a typo in its name
upstream (`FIELD_LEVELl_9`). Writing the golden set before the agent existed
meant the tests were not reverse-engineered from a system that already passed
them.

It also cost me. I reached hour 20 with a fully tested backend and no web
page, which is a hard requirement. See section 2.

Everything deterministic was built test-first. 356 tests now, 175 of them on
the SQL gate alone.

### How I used Claude Code

It wrote most of the code. The part worth describing is what happened after:
for nearly every feature I started a fresh code-review agent with no context
and asked it to find real defects. It caught things I would have shipped,
including raw Snowflake exception text leaking into user-facing messages, and
a bug where an unrelated geography lookup silently cleared a still-unresolved
ambiguity.

It never caught the worst bug in the project (section 4). The reviewers read
code, and the code was fine. The defect was in a prompt.

### Five decisions I would defend

**The SQL gate is the only real boundary.** Everything else, the system prompt
and the Haiku classifier, is advisory. `validate_sql` parses each query with
sqlglot and checks structure: one statement, SELECT only, every table against a
31-table allowlist, no star projection, `LIMIT` injected. It defaults to deny,
so anything sqlglot cannot model gets rejected rather than waved through.

I considered doing this with prompt rules plus a regex denylist and dropped it
after attacking my own first implementation and finding three bypasses in it.
If a purpose-built AST gate had three holes, a regex would have thirty.

At real scale the gate should not be the only control. A Snowflake role with
SELECT on exactly those 31 objects, plus a resource monitor, would contain a
gate bug at the database rather than at my parser.

**Variables are data, not prompt content.** There are 8,164 field codes in the
share and none of them appear in any prompt. The agent finds them through
FTS5 search over a local SQLite snapshot built at boot.

The alternative was a curated subset in the system prompt. That demos well and
breaks the moment someone asks about something I did not anticipate, and the
assignment warns against it directly.

I also chose lexical search over embeddings, and I got that partly right and
partly wrong. Lexical suits this corpus, because Census labels are formulaic
and the user's own words usually appear in them. But my first implementation
required every query token to match, and that turned out to cause the worst
retrieval failure in the project (section 3).

**Rules that say MUST are enforced in code.** Three of them: recovery stops
after 2 failed queries, the ambiguity check blocks Snowflake outright if a
geography is still unresolved, and a 50-second watchdog ends tool use and
answers from whatever rows already came back. All three messages are generated
by code, not by the model, so the model cannot talk past its own stop
condition.

All three started as prompt instructions. I moved them after watching them
fail. "At most 2 retries" is a promise about money and latency, and the model
cannot be the one keeping it.

**The median trap is encoded as data.** Counts can be summed across block
groups. Medians cannot. Rather than a sentence in the prompt,
`VariableHit.geo_levels` carries this per variable: count variables list all
five geography levels, median variables list block group only.

I did it this way because averaging block-group medians into a state median
produces a plausible number, with no error and nothing that looks wrong. Every
other failure in this system is loud. That one is silent, and silent wrong
numbers are exactly what a grounded agent exists to prevent.

**Two guardrails that fail in opposite directions.** The Haiku classifier fails
open: any error or a 1.5s timeout allows the question through. The SQL gate
fails closed. The classifier exists for speed, not safety. It refuses obvious
off-topic traffic in about 1.4 seconds instead of spending a full agent loop
on it.

Failing closed would trade a common real failure (a classifier outage blocking
every legitimate question) against one it does not actually prevent, since the
gate is what stops dangerous SQL either way.

---

## 2. What I would do differently

### Build the interface first

This is the big one. I built the guardrail, bounded recovery, ambiguity
handling, the watchdog, and degraded mode, all tested and all verified against
live Snowflake through raw HTTP, before noticing around hour 20 that there was
no web page at all.

The backend work was not wasted. But a reviewer cannot evaluate a curl
command, and typing two questions into a browser found the worst bug in the
project in under a minute. A bare chat page in hour 2 would have found it in
hour 2.

### What I cut, and what I got instead

**Langfuse became an in-app trace tab (D-021).** It works, and it is genuinely
useful for debugging a single turn: one span per guardrail check, per model
call with token counts, per tool call. It is also in-process and in-memory, so
it dies on restart, is invisible across replicas, and cannot answer anything
about the system as a whole. It is a debugging aid, and I would rather call it
that than claim the observability requirement is met.

**The eval harness shrank to 14 executed scenarios.** `make eval` does run for
real: live Anthropic, live Snowflake, the real guardrail, deterministic checks,
and a committed result file the Evals tab renders. The remaining gaps are named
precisely in section 3, but the headline one is that nothing runs the suite
automatically, so nothing catches a regression between commits. Everything here
was run by hand, by me, when I remembered to.

I also wrote 25 further scenarios covering injection shapes, malformed input,
NULL and top-coded values, and multi-turn drift, then deleted them (D-022).
They were marked as never-run and excluded from the pass rate, which was
honest, but they were two thirds of the table and made it materially harder to
read. Those gaps are listed in section 3 instead, which is the right home for a
claim with no evidence behind it.

**`normalize_value` is written, tested, and wired to nothing.** This is the cut
I am least comfortable with, because it is a live wrong-number path. 776 block
groups report `B19013e1 = 250001`, which is the Census code for "$250,000 or
more", not a median income of $250,001. Today that reaches the model as a bare
integer and will most likely be rendered as a precise figure. The number came
from the database and is still wrong in the way that matters. The fix is small
and I ran out of hours.

**Decennial tables cut (D-004), so the `conflicting` category has no real
coverage.** The design answered that requirement with a genuine second source,
where "population of Travis County" has two defensible answers the agent has to
surface and explain. Without it, conflict is covered only by the
median-versus-mean case, which is narrower than intended.

### If I had another day

1. Wire `normalize_value`. It is a live wrong-number path.
2. Make an eval able to fail when the agent does not answer at all (the
   `CMP-01` hole in section 3).
3. Finish the grounding check: the prose half, and the row visibility that
   currently makes `CMP-01` unverifiable.
4. Finish `PM-08`. Retrieval is fixed; what remains is the round cap.
5. Rebuild the deleted coverage as scenarios that actually run.
6. Rate limiting and a per-session cost cap.
7. Decennial tables, for a real conflicting case.
8. Real Langfuse.

---

## 3. Known failure modes I did not fully fix

Three groups, because they fail differently: things the agent can still get
wrong, things my evals cannot see, and things that would break under real
traffic. The middle group is the one I would read first if I were reviewing
this, because a limitation you cannot measure is worse than one you can.

### 3a. Wrong answers the system can still produce

- **Top-coded values reach the user as precise figures.** 776 block groups
  report `B19013e1 = 250001`, which is the Census code for "$250,000 or more",
  not an income of $250,001. `normalize_value` exists to catch this, is tested,
  and nothing calls it. The number comes from the database and is still wrong
  in the way that matters. This is the one I would fix first.
- **`PM-08` is genuinely flaky, and now I can prove it.** "Average household
  income in Texas" assembles a numerator and denominator and sometimes runs out
  of tool rounds doing it. My first diagnosis was wrong: I blamed the model for
  over-exploring. The real cause was retrieval requiring every query token to
  match, so "number of households" returned zero hits while the right variable
  sat in the snapshot the whole time (D-020, fixed). What remains is the
  8-round cap. Running the set three times at one commit measured it at **2
  passes in 3**, with the other 13 examples stable at 3 for 3. I kept it and
  reported the ratio rather than re-running until it went green.
- **Guardrail over-refusal of places that may not exist.** "Population of
  Atlantis" used to be refused as off-topic instead of reaching the tools and
  honestly reporting no match. Fixed (D-019) by having the classifier judge the
  *shape* of a question rather than whether the place exists, and pinned by
  `UN-08`. The underlying tension does not go away: the fast-fail path and the
  honest-not-found path compete for the same inputs.
- **US territories are missing.** 13 county-grain rows (American Samoa, Guam,
  Northern Mariana Islands, US Virgin Islands) have a null state in the source
  and were dropped rather than given an invented naming policy (D-012). Asking
  about Guam returns zero candidates. Honest, but the gap is invisible to the
  user.
- **2020 only.** The allowlist covers 2020 tables (D-003), because block groups
  were redrawn for 2020 and the 5-year windows overlap 4 of 5 years, so a
  comparison against 2019 is statistically invalid. I stand by enforcing that at
  the gate, but "how did this change since 2019" is a reasonable question this
  system structurally cannot answer.

### 3b. What my evals cannot see

This section exists because the honest answer to "do your evals prove the agent
works" is "partly, and here is exactly which part."

- **An example can pass while the agent never answers the question.** This is
  the sharpest one. `CMP-01` asks which of two counties has more people, and
  its checks are: did `resolve_geography` return `48453`, did it return
  `13121`, and did the stream terminate without an unhandled error. In one
  recorded run the agent resolved both counties, failed twice on the query, and
  replied *"I tried 2 times to get a grounded answer to this and couldn't."*
  All three checks passed. The scenario was green.

  Nothing was broken about the checks individually. Each one asserts something
  true and worth asserting. The gap is that **none of them asserts an answer
  exists**, so a well-behaved failure satisfies all of them. Bounded recovery
  is designed to produce exactly that kind of graceful failure, which means the
  better the failure handling, the easier it is for a scenario to pass without
  answering anything. `DF-05` does not have this problem, because it asserts
  the literal string `581,348`, so nothing but a real answer can satisfy it.
  The fix is to give every scenario that expects an answer something only a
  real answer can produce.

- **Numeric grounding is enforced, but only where numbers exist.** Rule 2 says
  every number must come from rows the query returned. That is now checked on
  every example automatically, deterministically: pull every figure of four or
  more digits out of the answer, and require each to be a returned value or a
  simple arithmetic combination of two of them. Deliberately not an LLM judge,
  because for a number there is nothing to judge and arithmetic is cheaper and
  more reliable. But in the latest run it verified figures on **4 of the 14
  examples**. Nine contain no numbers at all (refusals, clarifying questions,
  the city redirect), so there is nothing to ground. "Grounding passes 14/14"
  would be a true sentence and a misleading one.

- **`CMP-01`'s grounding is unverifiable, not verified.** The tool-end event
  exposes only the first row of a query result, deliberately, so a debug panel
  cannot become an unbounded payload. `CMP-01` returns more rows than that, so
  its figures cannot be traced and the check reports **inconclusive and
  passes**. Calling it a fabrication I cannot actually observe would be its own
  dishonesty, but the practical effect is that the comparison scenario, one of
  the few that produces multiple real numbers, is the one whose numbers go
  unchecked.

- **The prose half of grounding does not exist.** Whether the vintage
  assumption was stated, whether the median explanation is an explanation
  rather than the word "median", whether the county was offered rather than
  silently substituted: none of that is checked. `PM-02` asserts only that the
  answer contains "median". The live answer did explain it properly. The check
  would not have noticed if it hadn't. That half needs an LLM judge, and a
  judge needs calibrating against human labels before its scores mean anything,
  which is a project rather than an afternoon.

- **Nothing runs the evals automatically.** No CI gate, no regression check on
  commit. The Evals tab now shows a per-example history across commits so a
  regression is visible as one cell flipping, but somebody still has to run the
  suite and then look at it.

- **The recorded commit is not the whole story.** An `EvalRun` records
  `git_sha` but not the model ids, so a change in results could come from
  Anthropic rather than from me. It also cannot tell that the working tree was
  dirty, which it was for the run that produced the current `latest.json`.

### 3c. What would break under real traffic

- **No rate limiting and no spend cap.** A public endpoint that calls Anthropic
  and Snowflake on every request, behind one shared password, is unbounded cost
  exposure. Fine for a demo with a known reviewer list, not for anything else.
- **Single instance only.** Sessions are SQLite on a local volume and traces
  live in process memory. A second replica would split both. This cannot scale
  horizontally at all, not merely badly.
- **The watchdog cannot interrupt a call in flight.** It checks the clock
  between rounds, so one pathological model call can overrun the 50-second
  budget. Against a 60-second requirement that leaves 10 seconds of slack. The
  right fix is per-call timeouts and cancellation, not a bigger budget.
- **Health status can go stale.** Snowflake reachability is checked once at
  boot and cached (D-015), deliberately, to avoid touching Snowflake at request
  time outside `run_census_sql`. If Snowflake dies mid-session, `/api/health`
  keeps reporting healthy. Acceptable because nothing polls it continuously and
  a real outage still surfaces on the next query.

---

## 4. Testing

### What I test, and what I deliberately don't

356 tests, written test-first, on every layer where being wrong is either a
security hole or a silently wrong number: the SQL gate (175 on its own),
guardrail routing and both fail-open paths, recovery counting, the ambiguity
backstop, the watchdog against a faked clock rather than real sleeps, degraded
mode, session replay ordering, `normalize_value`, and the eval scorer itself.
The scorer got production-grade treatment because a bug there silently
invalidates every result.

I deliberately do not unit test whether the model phrases a good answer or
picks the right tool. Asserting on generated text through a mock tests the
mock. Golden evals are the right instrument, which is why the scenarios were
designed in the PRD before any agent code existed.

That split is right in principle. Here is where it failed completely.

### The bug that 280 passing tests could not see

I built the chat page, opened it, and typed the first two questions any
reviewer would type. "What's the population of Wyoming?" failed. "What about
Travis County, Texas?" failed. Every `run_census_sql` call in the system was
failing with `invalid identifier`.

The cause: SafeGraph stores ACS variable columns case-sensitively
(`B01001e23`), and Snowflake folds an unquoted identifier to uppercase, so
`SUM(B01003e1)` resolves to a column that does not exist. My own system
prompt's aggregation example showed the unquoted form. The prompt was teaching
the model the one pattern guaranteed to fail against this database.

The mocked suite could not have caught it, for three compounding reasons. The
gate passed the query correctly, because it is a safety boundary and whether a
column resolves is not its job. The Snowflake connector was stubbed, and a stub
returns rows for anything syntactically valid, so the fixture encoded the same
wrong belief as the code. And the defect lived in a prompt while its
consequence lived in Snowflake's identifier rules, with no artifact in between
for a unit test to hold.

**The part I find most instructive is that graceful degradation hid it.** The
bug had already surfaced twice before the browser found it, and both times I
recorded it as a success: the model hit the error, self-corrected to a quoted
identifier on retry, and I logged that as the agent handling a Snowflake quirk
well. Both notes were true. Both described a systematic prompt defect as normal
resilience. It only became visible when two questions in a row failed to
recover within budget.

The lesson generalizes: a good recovery path raises the cost of noticing the
thing it is recovering from. Every retry that succeeds is a failure nobody
investigates. If I did this again I would instrument recovery attempts as a
first-class metric with a threshold, because "the agent self-corrected" is a
signal, not a non-event.

The fix was an explicit highest-priority rule in the system prompt (every
`variable_id` reference must be double-quoted) plus quoting the example itself.
Verified live immediately after: Wyoming 581,348, Travis County 1,250,884.

And the harness I cut for time would have caught this in about 20 seconds.
`make eval` asserts a known number for Wyoming against the real stack; under
this bug that scenario fails loudly and names itself. The scenario design
existed from hour 1, and building the runner took about an hour when I finally
did it. I paid for that cut with roughly 18 hours of a completely broken system
that every test I had said was fine.

That is the whole argument for eval-first on an LLM system, in one incident:
the mocked suite tests my code, and the eval harness tests the system. Only one
of them was capable of noticing that the product did not work.

### The second incident: I trusted a check I had not calibrated

Late on, I built the numeric grounding check described in section 3b and ran it
against the real set. It went red twice, and both reds were bugs in the check.

The first run failed `DF-05` and `PM-02`. My number extractor was pulling
digits out of variable ids, so `B19013e1` became the "figure" 19013, and every
answer that cited a variable id was reported as a fabrication.

I fixed that, re-ran, and it failed `PM-02` and `PM-08`. This time the figures
were real: the answers reported a state-level mean, computed as aggregate
income divided by household count. My checker knew how to verify sums,
differences and percentages, and did not know how to verify division. I had
written a groundedness checker for a census agent and left out the one
arithmetic operation the product is built around, since dividing an aggregate
by a count is exactly what D-002 says to do when a median cannot be aggregated.

Zero real fabrications in either run. Two rounds of false accusations against
correct behaviour.

What makes this worth writing down is not the bugs, it is that I read the first
set of reds as findings about the agent. For a moment I believed the system was
fabricating numbers, because my instrument said so and my instrument was new. A
red row in a committed artifact looks like evidence about the system, and it is
equally likely to be evidence about the thing measuring it.

**A new eval check needs a calibration pass against known-good outputs before
any red it produces should be believed.** Run it against answers you have
already confirmed are correct, and treat every red as a suspected bug in the
check until proven otherwise. That is the same discipline as watching a test
fail before trusting it to pass, applied to the measuring instrument rather
than the code. I did not do it, and I got two rounds of confident wrong
answers from my own tooling.

A related, duller failure from the same session: the harness never called
`load_dotenv()`, though both its docstring and the Makefile claimed it needed
`.env`. Run outside a shell that already had the variables exported, every
scenario died on an authentication error, and the harness dutifully recorded
`0/14 passed` into a committed artifact. Nothing had been measured. It now
refuses to run at all when credentials are missing, because a run that measured
nothing must not produce a file that looks like a catastrophic regression.

### What I would add, in order

1. **A check that fails when the agent does not answer.** The `CMP-01` hole:
   right now a graceful failure satisfies every check a comparison scenario
   has. This is the cheapest fix on the list and closes the largest blind spot.
2. **The prose half of grounding**, with a judge calibrated against my own
   labels before I believe any score it produces.
3. **A live smoke test in CI** on any commit touching the system prompt,
   `src/tools.py`, or `src/sqlgate.py`. One real question, one known number,
   about 20 seconds and a few cents.
4. **A contract test against the real catalog**, asserting from
   `INFORMATION_SCHEMA.COLUMNS` that variable columns are mixed-case, so the
   assumption that broke everything is pinned by data rather than by a sentence
   in a prompt. Same for the schema beliefs that already bit me once.
5. **The deleted scenarios, as tests that run.** Malformed input, more
   injection shapes, NULL and top-coded values, multi-turn drift, worst-case
   latency.
6. **A regression gate**, so a pass-rate drop fails a commit instead of waiting
   for someone to look at a tab.
7. **Stronger checks.** The general fix is to test the checks themselves:
   mutate a known-good answer into a known-bad one and confirm the check goes
   red. A check that has never failed has not been tested. The grounding check
   above is the proof: it had never been seen to fail correctly, and its first
   two failures were both its own.
