# Censuschat Assignment-Optimization Build Spec

**Status:** Approved design, ready for implementation planning

**Date:** 2026-08-10

**Target:** One focused implementation day

**Governing source:** `docs/assignment.pdf`

**Strategy:** Correctness vertical slice first

## 1. Objective

Make censuschat's central correctness claim true at runtime and easy for an
assignment reviewer to verify.

The current application has a strong SQL security boundary, but final-answer
grounding, variable lineage, geography lineage, and value normalization are not
hard-enforced on the serving path. The committed eval suite can also report
green when the named behavior was not verified. This sprint repairs those two
credibility gaps before adding data breadth, UI surface, or an LLM judge.

The one-day outcome is:

1. Numeric claims are validated against complete evidence from the current
   turn before the user sees them.
2. SQL may reference Census variables and geography literals only when they
   came from this turn's discovery tools.
3. Suppressed and top-coded values are normalized before they enter model
   context.
4. Green core evals prove the named behavior rather than weak proxies.
5. A reviewer can exercise the important requirements through one guided
   conversation and verify the exact build and eval provenance.
6. The 60-second requirement is backed by hard per-call budgets and accurate
   readiness, not a round-boundary watchdog alone.

## 2. Assignment alignment

This spec optimizes the four evaluation dimensions in `docs/assignment.pdf`:

- **LLM / AI Engineering:** deterministic evidence lineage and final-answer
  validation surround the model's probabilistic decisions.
- **Production Quality:** failures are bounded, sanitized, nonblank, and
  terminated before the assignment deadline.
- **Judgment Under Constraints:** the sprint prioritizes truthfulness and
  verifiable behavior over additional data vintages, frameworks, and UI tabs.
- **Reflection and Self-Awareness:** every remaining limitation is stated as a
  specific deferred capability with an acceptance boundary.

## 3. Approved decisions

The following decisions are binding for P0:

- Buffer final answer prose until validation completes. Continue streaming
  status and tool events.
- Implement focused semantic validation for variable lineage, geography
  lineage, and vintage isolation.
- Do not implement complete aggregation-validity or numerator/denominator
  universe proof in P0.
- Permit one tool-free answer-repair call when at least eight seconds remain.
- Preserve the three public tools and the existing `ChatEvent` contract.
- Permit one additive, eval-only contract deviation, recorded as D-024.
- Make only narrow frontend changes: Guided Review and build/eval provenance.
- Enforce a real 50-second internal deadline and accurate readiness.
- Defer rate limits, spend quotas, tenancy, retention, and full tab
  consolidation.
- Do not add 2019 ACS, Decennial data, an agent framework, a provider
  abstraction, a dependency, or an LLM judge in P0.

## 4. P0 architecture

### 4.1 Request flow

The serving path becomes:

1. Load model-admitted session history.
2. Persist the user message for audit and run the existing guardrail.
3. If refused, persist both sides of the refused turn for audit but mark the
   rejected user content and canned assistant refusal as excluded from future
   model history.
4. Create one `TurnDeadline` and one empty `EvidenceLedger`.
5. Run the existing Sonnet tool loop.
6. Record variable-search and geography-resolution results in the ledger.
7. Before executing SQL, run the focused semantic gate against the SQL and
   ledger.
8. If semantic validation passes, run the existing static SQL gate and then
   Snowflake.
9. Record raw query rows in the ledger and create normalized model-facing rows.
10. Suppress text emitted during `tool_use` rounds from the user transcript.
11. Buffer the final `end_turn` text.
12. Validate its numeric claims against normalized current-turn query evidence.
13. If validation fails and at least eight seconds remain, perform one
    tool-free rewrite and revalidate it.
14. Emit and persist only the validated answer or deterministic failure.
15. Finalize the trace and emit exactly one terminal `done` or `error` event.

No unvalidated final-answer bytes may reach the client.

### 4.2 New internal modules

#### `src/evidence.py`

Owns turn-scoped evidence and immutable completion records. It must not perform
SQL parsing, validation, model calls, persistence, or rendering.

Required internal types:

```python
@dataclass(frozen=True)
class VariableEvidence:
    variable_id: str
    geo_levels: frozenset[str]
    years: frozenset[int]


@dataclass(frozen=True)
class GeographyEvidence:
    geo_id: str
    level: str
    display_name: str


@dataclass(frozen=True)
class QueryEvidence:
    sql: str
    variable_ids: frozenset[str]
    geo_ids: frozenset[str]
    raw_rows: tuple[Mapping[str, object], ...]
    model_rows: tuple[Mapping[str, object], ...]
    allowed_display_values: frozenset[str]


@dataclass
class EvidenceLedger:
    variables: dict[str, VariableEvidence]
    geographies: dict[str, GeographyEvidence]
    queries: list[QueryEvidence]


@dataclass(frozen=True)
class TurnRecord:
    guardrail_action: str
    guardrail_category: str | None
    tool_calls: tuple[ToolCallRecord, ...]
    evidence: LedgerSnapshot
    answer_validation: AnswerValidationRecord
    terminal_reason: str
    elapsed_ms: int
    model_usage: tuple[ModelUsageRecord, ...]
```

`EvidenceLedger` is mutable only during one turn. `TurnRecord` and
`LedgerSnapshot` are immutable. Full query rows are available to an in-process
observer but are not serialized into SSE events, session history, or durable
traces.

`agent_turn` accepts an optional keyword-only observer. Production passes no
observer. The eval runner supplies a collector and receives the completed
`TurnRecord`. This is an internal seam and does not change the HTTP or
`ChatEvent` contract.

#### `src/semantic_gate.py`

Owns context-sensitive SQL authorization. It runs before `validate_sql`, which
remains the static security boundary.

Required interface:

```python
def validate_semantic_sql(sql: str, ledger: LedgerSnapshot) -> SemanticGateResult:
    ...
```

The gate parses Snowflake SQL with the existing `sqlglot` dependency and:

- Collects every Census estimate column referenced by the statement.
- Rejects any estimate column absent from the ledger's variable-search hits.
- Collects geography literals applied to `CENSUS_BLOCK_GROUP`, including
  prefix predicates and `SUBSTR` predicates.
- Rejects any such literal that does not correspond to an unambiguous geography
  recorded in the ledger.
- Rejects any vintage other than `DEFAULT_VINTAGE`, even though the static
  table allowlist remains a second defense.
- Allows a query with no geography predicate so national aggregation remains
  representable.
- Returns the authorized variable and geography IDs for `QueryEvidence`.

The gate does not determine whether a median may be aggregated, whether two
universes are compatible, or whether the SQL computes the most useful answer.
Those are explicit P0 non-goals.

A semantic rejection becomes a sanitized tool error and consumes the existing
bounded recovery budget. It never reaches Snowflake.

#### `src/answer_gate.py`

Owns deterministic answer validation and repair instructions. It does not call
models itself.

Required interface:

```python
def validate_answer(answer: str, ledger: LedgerSnapshot) -> AnswerValidationResult:
    ...
```

It recognizes these data-claim forms:

- Currency values.
- Percentages.
- Comma-formatted numeric values.
- Plain values of four or more digits, except recognized ACS vintage years.
- Smaller values adjacent to a data unit such as people, households, counties,
  states, housing units, or workers.

Alphanumeric Census variable IDs and list ordinals are not data claims. The
recognized vintage metadata is `2016`, `2020`, and `5-year` when used in the
standard ACS vintage phrase.

A claim passes only when its exact display value is present in normalized rows
returned by `run_census_sql` during this turn. P0 does not reconstruct model
arithmetic. Differences, ratios, and percentages must be computed in SQL and
returned as result columns before they may appear in prose.

If a successful query returned numeric values, an answer with no permitted
numeric claim fails as a non-answer. Clarifications, refusals, zero-row
responses, and deterministic failures may contain no numeric data claims.

The gate also rejects common planning narration at the beginning of a final
answer, including `I'll look`, `Let me check`, `Found it`, and `Now I'll`.

On validator exceptions, the serving path fails closed and emits the
deterministic failure response.

#### `src/deadline.py`

Owns the absolute turn budget.

```python
@dataclass(frozen=True)
class TurnDeadline:
    expires_at: float

    def remaining_s(self) -> float: ...
    def require_budget(self, reserve_s: float = 0.0) -> float: ...
    def can_start(self, minimum_s: float) -> bool: ...
```

The deadline is created once with a 50-second budget. All model and tool calls
derive their timeout from the same expiration time. No round, repair, or tool
starts with fewer than eight seconds remaining.

### 4.3 Normalized result handling

`run_census_sql` continues to return the frozen `QueryResult` contract. The
agent creates a separate normalized serialization for model context.

- `NULL` becomes a nonnumeric `not reported` marker and is never converted to
  zero.
- A direct selected variable whose raw value matches a known top code becomes
  the bounded display phrase from `normalize_value`, such as `$250,000 or
  more`.
- Top-code detection applies only when a result expression maps directly to
  one source variable. It does not apply to aggregates whose numeric result
  happens to equal a top-code sentinel.
- Raw values remain in the in-memory ledger for debugging and eval evidence.
- Model-facing rows and allowed display values are stored alongside raw rows.

### 4.4 Buffered answer and repair

Text from any response with `stop_reason == "tool_use"` is neither emitted nor
persisted. Existing `tool_start`, `tool_end`, and `status` events retain
interactivity.

When a response ends the tool loop:

1. Emit a status event indicating answer verification.
2. Validate the buffered text.
3. If valid, emit it as the sole answer token payload and persist the same
   string.
4. If invalid and the deadline permits, call `AGENT_MODEL` once with no tools.
   The repair request contains the invalid answer, concise violations, and a
   bounded ledger summary of permitted facts and display values.
5. Revalidate the repair.
6. If repair is unavailable or invalid, emit:

   > I found relevant Census data, but I could not produce an answer whose
   > figures I could verify. Please try the question again.

The repair model may restate only permitted evidence. It may not call tools or
introduce derived values. Repair usage and outcome are traced.

### 4.5 Rejected history

Audit persistence and model-admitted history are separated. The SQLite message
table gains a `model_admitted INTEGER NOT NULL DEFAULT 1` column through an
idempotent startup migration. The internal append operation accepts the flag,
and a dedicated history query returns only admitted rows for Anthropic replay.

A rejected user message and its canned assistant refusal remain stored with
`model_admitted=0` for traceability and reviewer history. Both are excluded
from future model context, preserving valid user/assistant sequencing. Existing
allowed conversation history continues to replay unchanged in P0. The frozen
`ChatMessage` model does not change.

## 5. Evaluation redesign

### 5.1 Core and regression suites

The core suite contains exactly 12 scenarios:

1. `DF-05`, exact Wyoming population.
2. `DF-02`, exact Cook County household count.
3. `CMP-01`, both populations and the correct winner.
4. `MT-01`, exact population and household answers across two turns.
5. `MT-03`, exact numerator, denominator, and SQL-returned percentage.
6. `AMB-01`, geography clarification and no SQL.
7. `AMB-03`, geography and income-measure clarification and no SQL.
8. `PM-02`, invalid median aggregation rejected with valid mean substitution.
9. `PM-03`, explicit confirmation before a Travis County substitution.
10. `UN-01`, fast and nonempty projection refusal.
11. `OT-01`, off-topic guardrail path with no Sonnet or SQL.
12. `INJ-02`, injection guardrail path with no leakage or SQL.

The regression overlay contains:

- `UN-08`, unknown-subject routing.
- `PM-08`, mean-income retrieval and tool-round reliability.

`DF-01` and `AMB-02` remain available as historical scenarios but do not enter
the core score because they duplicate population retrieval and county-name
ambiguity already covered more strongly. Conflicting-source scenarios remain
visibly blocked until Decennial data exists and do not enter any pass-rate
denominator.

Before `DF-02` enters the core suite, its exact oracle is established by a
reviewed direct Snowflake query executed through the existing diagnostic path.
The reviewed SQL, variable ID, geography ID, exact result, and verification
date are recorded in the scenario notes. No guessed literal is permitted.

### 5.2 Scenario contract

Each core scenario declares:

- Suite: core or regression.
- Expected outcome: answer, clarify, refuse, or partial.
- Orthogonal tags for topic, geography level, dialogue shape, reasoning type,
  and safety behavior.
- Per-turn expected variables, geographies, tool sequence, forbidden tools,
  exact values or formula outputs, prohibited prose, and latency ceiling.
- A plain-language statement of what the scenario proves.

All answerable scenarios require a nonempty answer, successful SQL, expected
variable and geography lineage, exact returned values, and successful runtime
answer validation.

Clarification scenarios require the named ambiguity axes, a question, and no
SQL. Refusal scenarios require a nonempty scope explanation, the expected
guardrail or self-refusal mechanism, and no prohibited downstream work.

### 5.3 Eval observer and evidence

The eval runner supplies the internal turn observer and scores `TurnRecord`
objects. It no longer treats client-safe `TOOL_END.first_row` summaries as
complete evidence.

Per-turn observations retain:

- Full final answer.
- Guardrail verdict and category.
- Ordered tool calls and outcomes.
- Complete in-memory query rows.
- Runtime answer-validation result.
- Terminal reason, elapsed time, model rounds, token usage, and repair count.

Scenario-wide state is used only for explicitly scenario-wide assertions.
Current-turn grounding always uses current-turn query evidence.

### 5.4 D-024 eval-only contract deviation

D-024 authorizes additive changes to eval models in `src/contracts.py` while
leaving runtime tool and event models unchanged.

Required additions:

- `ScenarioSuite`: `core`, `regression`.
- `ScenarioOutcome`: `answer`, `clarify`, `refuse`, `partial`.
- `CheckStatus`: `pass`, `fail`, `unverified`.
- Check types for outcome, ordered tools, absent tools, exact returned value,
  forbidden answer content, per-turn assertion, and latency ceiling.
- `EvalScenario.suite`, `outcome`, and `tags` with backward-compatible
  defaults for historical artifacts.
- `CheckResult.status` while retaining the existing `passed` boolean for old
  UI compatibility. `unverified` sets `passed=False`.
- `EvalRun.provenance` containing Git SHA, dirty state, scenario-spec hash,
  scorer version, model IDs, prompt hash, and snapshot fingerprint.

Pass rates exclude `unverified` from both numerator and denominator. A run
with any unverified core scenario may not display a green core badge.

### 5.5 Human prose review

No LLM judge is added. After the first clean core run, a human reviews all 12
answers and records a binary label plus critique for:

- Correctness.
- Directness.
- Caveat quality.
- Actionability.

The review artifact includes the scenario ID, answer hash, reviewer, timestamp,
binary label, and critique. It is calibration data only and does not alter the
automated pass rate.

## 6. Deadline, readiness, and deployment truth

### 6.1 Deadline behavior

- Internal turn deadline: 50 seconds.
- Repair-start minimum: eight seconds remaining.
- Anthropic calls: explicit timeout no greater than the remaining budget.
- Snowflake: existing statement timeout plus bounded connector/network waits.
- Blocking tool work: awaited only within the remaining turn budget. A timed
  out worker thread may finish cleanup in the background, so connector-level
  timeouts remain mandatory even though the user stream has already ended.
- Timeout response: deterministic partial answer only when its figures pass the
  same answer gate; otherwise deterministic nonnumeric failure.
- Terminal invariant: exactly one `done` or `error` event.

Timing tests use an injected monotonic clock and fake calls. No test sleeps for
the real deadline.

### 6.2 Health semantics

Health is split into:

- `/livez`: process liveness.
- `/readyz`: validated snapshot and Snowflake query-path readiness.
- `/api/health`: backward-compatible summary using the same readiness state.
- `/api/version`: app version, eval Git SHA, scenario-spec hash, and snapshot
  timestamp.

A snapshot is ready only after schema and metadata validation succeeds. File
existence alone is insufficient. Chat readiness requires the validated local
snapshot and a last-known successful Snowflake query path.

Snowflake readiness is initialized by the bounded startup probe and updated
after every `run_census_sql` success or connection failure. Readiness endpoints
perform no new Snowflake query, preserving the single runtime access path.
Their payload identifies this as `last_known` state and includes the observation
timestamp, so it is not presented as a live probe.

Deployment verification inspects readiness JSON and expected app version. An
HTTP 200 with `ready=false` is a failed deployment.

## 7. Narrow reviewer experience

### 7.1 Guided Review

The Chat tab gains one collapsible Guided Review card. It preserves a single
session and provides these steps:

1. Ask for Harris County population.
2. Ask `What about households?`.
3. Ask for Washington County population, then answer `Oregon`.
4. Ask for median household income in California.
5. Ask for Texas population in 2050.
6. Open the existing turn evidence view.

Each step labels the requirement it proves. The card loads prompts but does not
auto-submit them, so the reviewer remains in control.

### 7.2 Eval provenance

The Evals tab displays separate summaries for:

- Core capability.
- Regression reliability.
- Failed scenarios.
- Unverified scenarios.
- Blocked coverage.

It also displays deployed app version, eval Git SHA, scenario-spec hash, and
snapshot fingerprint. A mismatch between the app and eval SHA is prominent and
cannot be rendered as a current green result.

### 7.3 Documentation truth sweep

The README opens with the same five-minute review path, the one-sentence request
flow, and a rubric map with `claim`, `evidence`, and `known limit` columns.

Update stale current-state claims about trace persistence, answer grounding,
prompt caching, Snowflake call count, and watchdog hardness. Historical
pre-code documents remain intact and receive targeted supersession annotations
only where required.

## 8. Error handling and observability

New trace spans:

- `semantic_gate`.
- `answer_validation`.
- `answer_repair` when attempted.

Every terminal path records a stable reason, including:

- `completed`.
- `guardrail_refused`.
- `semantic_recovery_exhausted`.
- `sql_recovery_exhausted`.
- `answer_repair_failed`.
- `deadline_exceeded`.
- `unexpected_error`.

App-level exceptions finalize the trace in `finally`, persist the exact emitted
failure response, and terminate the stream. Empty model output becomes a
deterministic nonblank failure.

Full query rows remain in memory only for the current turn and optional eval
observer. Durable traces store summaries, counts, IDs, validation decisions,
latencies, and token usage, not full result sets.

## 9. Test specification

All deterministic behavior follows red-green-refactor. Required tests include:

### `tests/test_evidence.py`

- Records variable hits, unambiguous geographies, raw rows, and normalized rows.
- Produces an immutable snapshot.
- Keeps turn evidence isolated across concurrent ledger instances.
- Does not serialize full rows into durable trace summaries.

### `tests/test_semantic_gate.py`

- Accepts a discovered variable and resolved state or county predicate.
- Rejects an undiscovered estimate column.
- Rejects an unresolved or ambiguous geography literal.
- Accepts national aggregation without a geography predicate.
- Rejects mixed or nondefault vintage references.
- Handles aliases, CTEs, quoted identifiers, `LIKE` prefixes, and `SUBSTR`
  predicates.
- Runs before the existing static SQL gate and before Snowflake.

### `tests/test_answer_gate.py`

- Accepts exact counts, money, percentages, and SQL-returned derived columns.
- Rejects unsupported counts, money, percentages, and small unit-bearing
  claims.
- Ignores variable IDs, list ordinals, and the recognized ACS vintage phrase.
- Rejects model-derived differences and ratios absent from query rows.
- Accepts normalized top-code display text and rejects the raw precise top-code
  value.
- Rejects a non-answer after a successful numeric query.
- Allows nonnumeric clarifications and refusals.
- Rejects planning narration.
- Fails closed on internal validator errors.

### `tests/test_agent.py`

- Does not emit or persist text from tool-use rounds.
- Emits and persists the same validated final answer.
- Attempts at most one tool-free repair with sufficient budget.
- Skips repair below the minimum budget.
- Falls back deterministically after invalid repair or repair error.
- Excludes refused user content from later model history.
- Emits exactly one terminal event on every exit path.
- Finalizes traces on unexpected exceptions and empty model output.

### `tests/test_deadline.py` and health tests

- Propagates one absolute budget across model and tool calls.
- Starts no expensive work below the minimum remaining budget.
- Returns before the injected 50-second deadline.
- Distinguishes liveness from readiness.
- Treats a corrupt snapshot as unready.
- Requires both validated snapshot and Snowflake path for chat readiness.
- Deployment verification rejects `ready=false` and version mismatch.

### Eval scorer tests

- Scores every turn in a multi-turn scenario.
- Requires exact values and ordered tools for answerable scenarios.
- Distinguishes guardrail refusal from model self-refusal.
- Treats incomplete row evidence as `unverified`, never pass.
- Excludes unverified and blocked coverage from pass-rate arithmetic.
- Separates core capability from regression reliability.
- Detects incompatible scenario-spec, scorer, model, prompt, or snapshot hashes.

The full offline suite must pass before any live eval or commit containing code.

## 10. One-day work packages and cut order

Implementation planning must decompose P0 into these ordered packages:

1. **Evidence foundation:** ledger, immutable records, observer seam, semantic
   gate, answer gate, and their unit tests.
2. **Agent integration:** tool-result normalization, buffered output, one repair,
   rejected-history filtering, trace finalization, and agent tests.
3. **Eval credibility:** D-024, full observer evidence, 12-core registry,
   regression overlay, tri-state scoring, provenance, and scorer tests.
4. **Deadline and readiness:** absolute budget, endpoint semantics, deployment
   verification, and deterministic tests.
5. **Reviewer surface:** Guided Review, provenance display, and documentation
   truth sweep.
6. **Verification:** full offline suite, explicit approval for paid calls, one
   clean core run, three regression repetitions, human review, and committed
   artifacts.

If time expires, stop before package 5 and report the sprint as partially
complete rather than silently weakening packages 1 through 4. Packages 1 and 2
are indivisible. Do not ship a partially connected evidence ledger or a
validator that runs only in evals. A paid eval is never run without explicit
approval, even when it is a release gate.

## 11. P0 acceptance criteria

P0 implementation is complete when all of the following are true:

- No text from a tool-use round appears in the user transcript or persisted
  assistant history.
- Every user-visible numeric data claim is accepted by the runtime answer gate
  against this turn's complete normalized query evidence.
- Every SQL estimate column and geography literal has current-turn discovery
  lineage.
- Suppressed values never become zero and top-coded values never render as
  precise raw figures.
- A failed answer gets at most one repair and then a deterministic nonnumeric
  response.
- Refused user content does not enter later model context.
- Every turn obeys the absolute deadline and emits exactly one terminal event.
- Readiness is false for a missing or invalid snapshot or an unavailable
  Snowflake query path.
- The 12 core scenarios have strong per-turn checks and no green-unverified
  state.
- Regression reliability is displayed separately from the core score.
- Eval artifacts identify the exact app, prompt, scorer, scenario set, models,
  and snapshot they measured.
- Guided Review and README present the same coherent reviewer path.
- The full offline suite passes.
- A code-reviewer subagent returns PASS or all blocking findings are resolved
  before implementation is reported complete.

Live-eval and human-review criteria remain blocked, rather than silently
waived, until paid-call approval is given.

## 12. Later phases

### Phase 2: Reviewer and multiuser hardening

Scope:

- Consolidate the UI into Chat, Evals, and Evidence.
- Add message and session bounds, per-session serialization, global concurrency
  limits, rate limits, and spend quotas.
- Bind sessions and traces to authenticated principals.
- Add SQLite indexes, WAL, busy timeout, retention, and pagination.
- Bound model-context selection while retaining complete stored history.
- Verify expected version and readiness during deployment and retain a rollback
  target.

Acceptance boundary: two authenticated users cannot read or mutate one
another's sessions, concurrent turns cannot interleave history, overload is
bounded, and deployment success proves the expected revision is ready.

### Phase 3: Semantic breadth

Scope:

- Enforce aggregation validity from variable metadata.
- Enforce numerator and denominator universe compatibility.
- Add 2020 Decennial data as a distinct source with source-aware retrieval and
  SQL validation.
- Implement conflicting-source scenarios that explain ACS estimate versus
  Decennial count.
- Permit 2015-2019 ACS only as an explicit alternate vintage with mixed-vintage
  SQL prohibited.

Acceptance boundary: invalid statistical aggregations and universe mismatches
are rejected before Snowflake, and source conflicts produce two labeled,
grounded figures with an accurate explanation of why they differ.

### Phase 4: Calibrated prose evaluation

Scope:

- Accumulate at least 30 human-reviewed outputs with binary labels and written
  critiques.
- Build narrow judges only for recurring failure types that deterministic
  checks cannot express.
- Measure agreement against human labels and retain disagreements for error
  analysis.

Acceptance boundary: no judge affects release decisions until it exceeds 90
percent agreement with the human labels on a held-out calibration set.

## 13. Explicit non-goals

This master spec does not authorize:

- A fourth agent tool.
- An agent framework.
- A new model provider or generic provider abstraction.
- Embeddings or a vector database.
- New Census data during P0.
- A universal LLM judge.
- Silent deletion or editing of red eval artifacts.
- Deployment or paid live evals without explicit approval.

These exclusions are part of the design, not missing implementation detail.
