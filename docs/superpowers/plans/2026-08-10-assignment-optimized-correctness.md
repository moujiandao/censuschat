# Assignment-Optimized Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make censuschat's runtime grounding claim true, make its core eval score defensible, and give an assignment reviewer one coherent path through Chat, Evidence, Evals, and Trust Rules.

**Architecture:** Add a turn-scoped evidence ledger around the existing three-tool agent loop. Authorize SQL against current-turn variable and geography lineage before the existing static SQL gate, normalize query results before model context, buffer final prose until a deterministic answer gate accepts it, and expose immutable turn records to the eval runner through an internal observer. Preserve the public tool and `ChatEvent` contracts. Use one absolute deadline across model and tool work, and derive readiness and eval provenance from validated state rather than optimistic proxies.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, Anthropic SDK, sqlglot, Snowflake connector, SQLite, vanilla JavaScript in one static HTML file, pytest.

## Global Constraints

- Treat `docs/superpowers/specs/2026-08-10-censuschat-assignment-optimization-design.md` as the governing specification and `docs/assignment.pdf` as the motivation.
- Preserve exactly three public tools: `search_census_variables`, `resolve_geography`, and `run_census_sql`.
- Preserve the existing `ChatEvent` enum and payload contract. Final answer prose may be buffered and emitted as one `token` event.
- Keep `validate_sql` as the static security boundary. The new semantic gate runs before it and never replaces it.
- Never expose unvalidated final-answer bytes to the client or persist them to session history.
- Never serialize complete query rows into SSE, session history, or durable traces. Full rows may exist only in the current-turn ledger and optional in-process eval observer.
- Add no dependency, frontend build step, agent framework, provider abstraction, new Census vintage, or LLM judge.
- Use red-green-refactor for every deterministic layer. Run `.venv/bin/pytest -q` before every code commit because bare `pytest` resolves to the wrong local interpreter in this workspace.
- Preserve unrelated worktree changes and stage only the files named in each task.
- Do not run `make eval`, a direct Snowflake oracle query, `make deploy`, or `deploy.sh` without Brian's explicit approval at that point.
- The approved spec names the eval contract deviation D-024. Before writing it, inspect the latest committed decision ID. If D-024 has been claimed by another workstream, use the next free ID and update generated references. Never renumber an existing decision.
- Tasks 1 through 8 below form the indivisible runtime correctness slice. If time expires, stop before the reviewer-surface package rather than leaving a gate wired only into evals or a ledger disconnected from serving.
- After code changes are complete, invoke the required code-reviewer subagent with the task description and exact changed-file list. Resolve every BLOCKING finding before reporting completion.

## Fast-Build Execution Profile

Keep every TDD case required by the approved spec. TDD covers all deterministic
trust boundaries, state transitions, scoring, readiness behavior, and named
regressions. Build speed comes from avoiding redundant tests and speculative
abstractions, not from reducing behavioral coverage. Passive CSS declarations,
documentation prose, and stochastic model wording remain outside unit-test
scope because structural UI tests, review, and live evals own those concerns.

- Start each behavior cluster with one failing acceptance test or one compact
  parameterized matrix, not one test per helper or AST node.
- Implement the shortest path that makes the cluster pass. Extract a helper
  only when two call sites or a testability boundary require it.
- Add another test for every named invariant and boundary in the approved spec,
  plus any failure actually observed during implementation. Combine equivalent
  cases into parameterized matrices without dropping assertions.
- Run only the owning test files during red-green cycles. Run the full offline
  suite immediately before each commit and at each checkpoint.
- The current offline suite takes about 2.2 seconds for 375 tests, so full-suite
  verification is not a meaningful build-time bottleneck. Keep it.
- Test UI structure and rule-catalog completeness once. Use one manual browser
  pass for appearance and interaction instead of unit-testing DOM helpers.
- Keep model prose out of unit tests. Validate live behavior through the 12 core
  scenarios only after paid-call approval.
- Do not perform opportunistic refactors, provider abstraction, schema
  generalization, or Phase 2 hardening while a P0 acceptance test is red.

## File Map

| File | Responsibility after this plan |
|---|---|
| `src/evidence.py` | Turn-local mutable ledger, immutable snapshots, tool/model usage records, and immutable completed turn record. |
| `src/semantic_gate.py` | Context-sensitive SQL authorization against variable, geography, and vintage evidence. |
| `src/answer_gate.py` | Model-facing row normalization, permitted display values, numeric-claim extraction, final-answer validation, and repair instructions. |
| `src/deadline.py` | One injectable monotonic deadline shared by every model and tool operation. |
| `src/agent.py` | Orchestration only: populate evidence, enforce gates, buffer prose, repair once, finalize one trace and one terminal event. |
| `src/sessions.py` | Full audit transcript plus a separate model-admitted history query. |
| `src/snapshot.py`, `src/tools.py` | Synthetic nation row and exact nation aliases; existing state/county behavior remains intact. |
| `src/health.py`, `src/app.py` | Validated snapshot state, last-known Snowflake state, liveness, readiness, health, version, and observer-safe stream backstop. |
| `src/contracts.py` | Additive eval-only D-024 models. Runtime tool, message, and event models stay frozen. |
| `evals/scenarios.py` | Exactly 12 core scenarios, two regression scenarios, and visibly blocked coverage outside denominators. |
| `evals/run_evals.py` | Per-turn observer scoring, tri-state checks, exact evidence, separated suite rates, and run provenance. |
| `static/index.html` | Exactly four tabs in order: Chat, Evidence, Evals, Trust Rules. One Evidence state path, Guided Review, provenance, and 72 trust rules. |
| `deploy.sh`, `Dockerfile`, `docker-compose.yml`, `.env.example` | Immutable app version injection and readiness-aware deployment verification. |
| `README.md`, `docs/reflection.md`, `docs/01-architecture.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/decisions.md` | Current-state truth sweep, rubric map, decision record, and architecture map. |

---

### Task 1: Give national queries explicit geography lineage

**Files:**
- Modify: `tests/test_snapshot.py`
- Modify: `tests/test_tools.py`
- Modify: `src/snapshot.py`
- Modify: `src/tools.py`

**Interfaces:**
- Existing: `_expand_geo_rows(county_rows) -> list[dict[str, Any]]`
- Existing: `resolve_geography(name, level_hint=None) -> GeoResolution`
- Required synthetic row: `geo_id="US"`, `level="nation"`, `name="United States"`

- [ ] **Step 1: Write the failing snapshot and resolver tests**

Add a snapshot test that asserts exactly one nation row is added even when multiple county rows are expanded. Add parameterized resolver tests for `United States`, `US`, and `USA`, each expecting one unambiguous `GeoCandidate` with `geo_id == "US"` and `level == GeoLevel.NATION`.

```python
def test_expand_geo_rows_adds_exactly_one_nation():
    expanded = snapshot._expand_geo_rows([_alameda_row(), _cook_row()])
    nations = [row for row in expanded if row["level"] == "nation"]
    assert nations == [{
        "level": "nation",
        "geo_id": "US",
        "name": "United States",
        "state": "US",
        "state_fips": "",
        "county": None,
        "county_fips": None,
    }]

@pytest.mark.parametrize("alias", ["United States", "US", "USA"])
def test_nation_aliases_resolve_exactly(alias, monkeypatch):
    result = tools.resolve_geography(alias)
    assert result.ambiguous is False
    assert [(c.geo_id, c.level) for c in result.candidates] == [
        ("US", GeoLevel.NATION)
    ]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest tests/test_snapshot.py tests/test_tools.py -k 'nation' -q`

Expected: FAIL because no nation row or alias path exists.

- [ ] **Step 3: Add the row once and resolve aliases before general ranking**

Prepend one canonical nation dictionary in `_expand_geo_rows`. In `resolve_geography`, normalize case and surrounding whitespace, match `united states`, `us`, and `usa`, then query the existing local geography table for `geo_id="US"`. Do not synthesize a candidate at request time, because the ledger must prove the same snapshot-backed discovery path used by other geographies.

- [ ] **Step 4: Run focused and regression tests**

Run: `.venv/bin/pytest tests/test_snapshot.py tests/test_tools.py -q`

Expected: PASS, including existing county ambiguity, state abbreviation, and unsupported-geography tests.

- [ ] **Step 5: Commit the geography slice**

```bash
git add src/snapshot.py src/tools.py tests/test_snapshot.py tests/test_tools.py
git commit -m "feat: add explicit nation geography lineage"
```

---

### Task 2: Build the turn-scoped evidence foundation

**Files:**
- Create: `src/evidence.py`
- Create: `tests/test_evidence.py`

**Interfaces:**
- Produces: `VariableEvidence`, `GeographyEvidence`, `QueryEvidence`, `ToolCallRecord`, `ModelUsageRecord`, `AnswerValidationRecord`, `LedgerSnapshot`, `EvidenceLedger`, and `TurnRecord`.
- No module in this task performs SQL parsing, model calls, persistence, or browser rendering.

- [ ] **Step 1: Write failing ledger isolation and immutability tests**

Cover recording variable hits, recording only unambiguous geography, recording raw and model rows, deriving allowed display values, freezing a snapshot, and ensuring two ledger instances never share state. Assert `dataclasses.FrozenInstanceError` when a snapshot field is mutated.

```python
def test_ledger_snapshot_is_immutable_and_isolated():
    first = EvidenceLedger()
    second = EvidenceLedger()
    first.record_variable(_variable_hit("B01003e1"))
    first.record_geography(_geo_resolution("06", "state"))
    snapshot = first.snapshot()

    assert set(snapshot.variables) == {"B01003e1"}
    assert set(snapshot.geographies) == {"06"}
    assert second.snapshot().variables == {}
    with pytest.raises(FrozenInstanceError):
        snapshot.queries = ()
```

Add a serialization boundary test that calls the trace-summary helper and asserts sentinel raw values and complete rows are absent while row counts, column names, variable IDs, and geography IDs remain.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_evidence.py -q`

Expected: collection ERROR because `src.evidence` does not exist.

- [ ] **Step 3: Implement explicit records and copy-on-snapshot**

Use frozen dataclasses for completed records and `MappingProxyType` or copied mappings for snapshot dictionaries. Store query rows as tuples of copied mappings. Keep mutation methods narrow:

The concrete public surface is `EvidenceLedger()` plus
`record_variable_search(result)`, `record_geography_resolution(result)`,
`record_query(evidence)`, and `snapshot()`. Each method returns `None` except
`snapshot()`, which returns `LedgerSnapshot`.

`record_geography_resolution` records a candidate only when `ambiguous is False` and exactly one candidate exists. The durable-summary method returns metadata only, never `raw_rows` or `model_rows`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_evidence.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the evidence primitives**

```bash
git add src/evidence.py tests/test_evidence.py
git commit -m "feat: add turn-scoped evidence ledger"
```

---

### Task 3: Authorize SQL against current-turn semantic evidence

**Files:**
- Create: `src/semantic_gate.py`
- Create: `tests/test_semantic_gate.py`

**Interfaces:**
- Produces: `validate_semantic_sql(sql: str, ledger: LedgerSnapshot) -> SemanticGateResult`
- `SemanticGateResult` contains `ok`, sanitized user-safe `violations`, authorized `variable_ids`, authorized `geo_ids`, and direct output-column lineage used by normalization.
- Consumes the existing `DEFAULT_VINTAGE` and sqlglot Snowflake parser.

- [ ] **Step 1: Write the failing semantic authorization matrix**

Create table-driven tests for:

- discovered variable plus resolved state prefix;
- discovered variable plus resolved county prefix;
- undiscovered estimate column;
- unresolved literal and ambiguous geography;
- nation evidence authorizing an unfiltered aggregation;
- the same unfiltered aggregation without nation evidence;
- a nondefault or mixed vintage;
- quoted identifiers, aliases, CTEs, `LIKE '06%'`, `SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) = '06'`, and county-length prefixes;
- direct result aliases mapped to their source variable, while aggregate aliases map to `None`.

```python
def test_unfiltered_query_requires_nation_evidence():
    result = validate_semantic_sql(
        'SELECT SUM("B01003e1") AS population FROM US_CENSUS.PUBLIC."2020_CBG_B01"',
        ledger_without_nation(),
    )
    assert result.ok is False
    assert "nation geography was not resolved" in result.detail
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_semantic_gate.py -q`

Expected: collection ERROR because `src.semantic_gate` does not exist.

- [ ] **Step 3: Implement a focused AST walk**

Parse with `sqlglot.parse(sql, dialect="snowflake")`. Compare normalized quoted column names against ledger variable IDs. Extract only CBG geography predicates the P0 spec authorizes. Reject dynamic or unrecognized geography expressions instead of guessing. Derive geography authorization as follows:

- 2-character prefix maps to a resolved state `geo_id`.
- 5-character prefix maps to a resolved county `geo_id`.
- no CBG predicate requires resolved `geo_id="US"` at `level="nation"`.

Return stable violation codes such as `variable_not_discovered`, `geography_not_resolved`, and `vintage_not_allowed`. Do not implement median aggregation or universe compatibility in this module.

- [ ] **Step 4: Prove the semantic gate is additive to the static gate**

Add one test where semantic validation passes but `validate_sql` rejects a banned construct. Assert these are separate results. The serving-order assertion lands in Task 7.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_semantic_gate.py tests/test_sqlgate.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the semantic gate**

```bash
git add src/semantic_gate.py tests/test_semantic_gate.py
git commit -m "feat: enforce current-turn SQL lineage"
```

---

### Task 4: Normalize query rows and validate final-answer claims

**Files:**
- Create: `src/answer_gate.py`
- Create: `tests/test_answer_gate.py`
- Modify: `tests/test_normalize_value.py`

**Interfaces:**
- Produces: `normalize_query_rows(result: QueryResult, column_lineage: Mapping[str, str | None]) -> NormalizedQueryRows`
- Produces: `validate_answer(answer: str, ledger: LedgerSnapshot) -> AnswerValidationResult`
- Produces: `build_repair_instruction(answer, validation, ledger) -> str`
- Reuses: `normalize_value(raw, variable_id)` from `src/contracts.py`.

- [ ] **Step 1: Write failing normalization tests**

Assert:

- SQL `NULL` becomes `"not reported"`, never `0`;
- direct `B19013e1 == 250001` becomes `"$250,000 or more"`;
- an aggregate or unrelated alias equal to `250001` remains numeric;
- raw rows and normalized rows are distinct objects;
- allowed display values include canonical comma, currency, decimal, and percentage forms actually present in normalized rows.

```python
def test_top_code_only_applies_to_direct_variable_lineage():
    result = QueryResult(
        columns=["median_income", "aggregate_value"],
        rows=[{"median_income": 250001, "aggregate_value": 250001}],
        row_count=1,
    )
    normalized = normalize_query_rows(
        result,
        {"median_income": "B19013e1", "aggregate_value": None},
    )
    assert normalized.rows[0]["median_income"] == "$250,000 or more"
    assert normalized.rows[0]["aggregate_value"] == 250001
```

- [ ] **Step 2: Write failing claim-recognition and validation tests**

Cover exact counts, money, percentages, SQL-returned derived values, unsupported four-digit figures, small unit-bearing figures, raw top codes, model-derived differences and ratios, variable IDs, list ordinals, `2016-2020 5-year`, planning narration, numeric-query nonanswers, and nonnumeric clarification/refusal paths.

Use strict equality after display canonicalization. Do not permit arithmetic tolerance or recomputation in Python.

- [ ] **Step 3: Run and verify RED**

Run: `.venv/bin/pytest tests/test_answer_gate.py tests/test_normalize_value.py -k 'normalize or answer or top_code' -q`

Expected: FAIL because the normalization and answer-gate interfaces do not exist.

- [ ] **Step 4: Implement normalization without mutating the frozen tool result**

For each cell, pass the direct source variable only when `column_lineage[column]` is non-null. Format top-code bands and `not reported` explicitly. Build the allowed set from the normalized values, including only deterministic equivalent renderings. Do not add geography IDs or SQL literals to the allowed numeric set.

- [ ] **Step 5: Implement deterministic claim extraction and validation**

Recognize currency, percentages, comma-formatted values, plain values with four or more digits except recognized vintage framing, and smaller values adjacent to the approved units. Exclude alphanumeric variable IDs and list ordinals. Fail closed on validator exceptions.

```python
@dataclass(frozen=True)
class AnswerValidationResult:
    ok: bool
    claims: tuple[str, ...]
    unsupported_claims: tuple[str, ...]
    violations: tuple[str, ...]
```

If a successful query returned numeric evidence, require at least one permitted numeric claim. If no query succeeded or every successful query returned zero rows, allow a nonnumeric clarification, refusal, not-found response, or deterministic failure, but reject any numeric data claim. Reject answers beginning with `I'll look`, `Let me check`, `Found it`, or `Now I'll` case-insensitively.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_answer_gate.py tests/test_normalize_value.py -q`

Expected: PASS.

- [ ] **Step 7: Commit normalization and answer validation**

```bash
git add src/answer_gate.py tests/test_answer_gate.py tests/test_normalize_value.py
git commit -m "feat: validate answers against normalized evidence"
```

---

### Task 5: Separate audit history from model-admitted context

**Files:**
- Modify: `tests/test_sessions.py`
- Modify: `src/sessions.py`

**Interfaces:**
- Preserve: `get_session(session_id) -> Session` returns the complete audit transcript.
- Add: `get_model_session(session_id) -> Session` returns only `model_admitted=1` rows.
- Extend internal write: `append_message(session_id, msg, *, model_admitted: bool = True) -> None`.

- [ ] **Step 1: Write failing migration and filtering tests**

Create a legacy SQLite database with the four existing columns, call the session layer, and assert an idempotent migration adds `model_admitted INTEGER NOT NULL DEFAULT 1`. Persist admitted and rejected user/assistant pairs. Assert `get_session` returns all rows while `get_model_session` returns admitted rows only and preserves valid user/assistant ordering.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_sessions.py -k 'admitted or migration' -q`

Expected: FAIL because the column, flag, and filtered query do not exist.

- [ ] **Step 3: Implement the idempotent SQLite migration**

In `_sqlite_connect`, inspect `PRAGMA table_info(messages)`. When the column is absent, execute:

```sql
ALTER TABLE messages
ADD COLUMN model_admitted INTEGER NOT NULL DEFAULT 1
```

Use parameterized integer writes (`1` or `0`). Keep `get_session` unchanged in meaning. Add the filtered query separately instead of adding a boolean parameter to the audit API.

- [ ] **Step 4: Run all session tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_sessions.py -q`

Expected: PASS, including legacy persistence and cross-session isolation.

- [ ] **Step 5: Commit the history boundary**

```bash
git add src/sessions.py tests/test_sessions.py
git commit -m "feat: separate audit and model history"
```

---

### Task 6: Enforce one absolute turn deadline

**Files:**
- Create: `src/deadline.py`
- Create: `tests/test_deadline.py`

**Interfaces:**
- Produces: `TurnDeadline.start(budget_s=TURN_DEADLINE_S, clock=time.monotonic) -> TurnDeadline`
- Produces: `remaining_s()`, `require_budget(reserve_s=0.0)`, and `can_start(minimum_s)`.

- [ ] **Step 1: Write failing fake-clock tests**

Cover initial budget, elapsed budget, reserve rejection, minimum-start threshold, and expiry. No test may sleep.

```python
def test_deadline_uses_one_absolute_expiration():
    clock = FakeClock(100.0)
    deadline = TurnDeadline.start(50.0, clock=clock)
    clock.advance(42.1)
    assert deadline.can_start(8.0) is False
    with pytest.raises(DeadlineExceeded):
        deadline.require_budget()
```

The threshold test must define equality explicitly: exactly eight seconds permits work, less than eight does not.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_deadline.py -q`

Expected: collection ERROR because `src.deadline` does not exist.

- [ ] **Step 3: Implement the immutable deadline**

Store the clock callable as a non-comparable, non-repr dataclass field. Clamp `remaining_s()` at zero. `require_budget(reserve_s)` returns remaining time available to the caller after reserve and raises a stable `DeadlineExceeded` when nonpositive.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_deadline.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the deadline primitive**

```bash
git add src/deadline.py tests/test_deadline.py
git commit -m "feat: add absolute turn deadline"
```

---

### Task 7: Connect evidence, semantic validation, and normalized rows to the agent loop

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `src/agent.py`
- Modify: `src/tools.py`

**Interfaces:**
- Extend internal API: `agent_turn(session_id, user_message, *, observer: Callable[[TurnRecord], None] | None = None, deadline: TurnDeadline | None = None) -> AsyncIterator[ChatEvent]`.
- Keep HTTP callers unchanged.
- Add internal tool result envelope or dispatch helper that retains typed results for the ledger while preserving the existing client-safe `TOOL_END.summary`.

- [ ] **Step 1: Write failing agent tests for ledger population and serving order**

Assert variable search and unambiguous geography results populate the ledger. Assert `validate_semantic_sql` is called before `run_census_sql`, and a semantic rejection never calls Snowflake. Assert rejection consumes the existing recovery budget and is exposed to the model as a sanitized tool error.

- [ ] **Step 2: Write failing normalization and hidden-tool-prose tests**

Use one fake `tool_use` model round containing narration plus a SQL call, followed by one `end_turn` answer. Assert narration is absent from token events and session history. Assert the second model request sees normalized rows (`not reported` and top-code band) while the observer ledger retains raw rows.

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `.venv/bin/pytest tests/test_agent.py -k 'semantic or ledger or normalized or tool_round_text' -q`

Expected: FAIL because the agent does not own a ledger, semantic gate, observer, or normalized tool result.

- [ ] **Step 4: Refactor tool dispatch around typed internal results**

Keep `_summarize_tool_result` bounded and user-safe. After each tool completes:

- record variable search results;
- record only unambiguous geography resolution;
- before SQL, snapshot the ledger and run semantic validation;
- call the existing `run_census_sql` only after semantic success;
- normalize SQL result rows using direct column lineage;
- append `QueryEvidence` with raw and model rows;
- send only normalized rows to Sonnet.

The static `run_census_sql` gate remains in `src/tools.py`. Last-known Snowflake telemetry is connected in Task 11, where its state and failure semantics receive dedicated tests.

- [ ] **Step 5: Suppress intermediate prose**

For a response whose `stop_reason == "tool_use"`, ignore text blocks for client and persistence. Continue emitting `status`, `tool_start`, and `tool_end`. Only the eventual `end_turn` text becomes a final-answer candidate.

- [ ] **Step 6: Run agent, tool, and trace regression tests**

Run: `.venv/bin/pytest tests/test_agent.py tests/test_tools.py tests/test_tracing.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the connected evidence path**

```bash
git add src/agent.py src/tools.py tests/test_agent.py
git commit -m "feat: connect evidence lineage to the agent loop"
```

---

### Task 8: Buffer, validate, repair, and terminate every answer safely

**Files:**
- Modify: `tests/test_agent.py`
- Modify: `tests/test_app.py`
- Modify: `src/agent.py`
- Modify: `src/app.py`

**Interfaces:**
- Uses the Task 4 answer gate and Task 6 deadline.
- Produces exactly one completed `TurnRecord` through the optional observer on every terminal path.
- Persists exactly the same text emitted in the sole final-answer token payload.

- [ ] **Step 1: Write failing answer-buffer and repair tests**

Cover:

- a valid answer emits once and persists identically;
- an invalid answer with at least eight seconds remaining triggers exactly one no-tools repair call;
- a valid repair is revalidated and emitted;
- invalid repair, repair exception, or insufficient time emits the deterministic no-number fallback;
- empty model output emits a nonblank deterministic failure;
- a refused user message and canned refusal are persisted with `model_admitted=0` and absent from the next Anthropic history;
- deadline expiry starts no new expensive work;
- every success, refusal, recovery exhaustion, timeout, and unexpected error produces one terminal event and one observer record.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest tests/test_agent.py tests/test_app.py -k 'answer or repair or admitted or terminal or deadline or unexpected' -q`

Expected: FAIL because final prose is currently streamed before validation and exception finalization is split across layers.

- [ ] **Step 3: Centralize terminal finalization in `agent_turn`**

Use one internal finalizer that accepts `answer_text`, `terminal_reason`, `EventType`, and validation record. It must:

1. persist exactly `answer_text` with the appropriate admission flag;
2. emit the answer token only when nonempty and safe;
3. finish the durable trace with metadata only;
4. notify the observer once with an immutable `TurnRecord`;
5. emit exactly one `done` or `error` event.

Stable terminal reasons are `completed`, `guardrail_refused`, `semantic_recovery_exhausted`, `sql_recovery_exhausted`, `answer_repair_failed`, `deadline_exceeded`, and `unexpected_error`.

- [ ] **Step 4: Add the one-call repair path**

Before repair, require `deadline.can_start(8.0)`. Call `AGENT_MODEL` with no tools and an explicit SDK timeout no greater than `deadline.remaining_s()`. Provide only the invalid draft, concise violations, and a bounded normalized evidence summary. Revalidate the repair against the same immutable ledger snapshot.

- [ ] **Step 5: Bound all model and blocking tool calls**

Pass an explicit remaining timeout to Anthropic. Wrap each `asyncio.to_thread` tool operation with `asyncio.timeout(deadline.require_budget())`. Keep connector and statement timeouts because cancelling the await cannot stop a worker thread already performing cleanup.

- [ ] **Step 6: Keep `_stream_turn` as a terminal backstop**

The app wrapper still converts a contract violation into a sanitized error event. Normal agent exceptions must now be finalized inside `agent_turn`, so add an endpoint test proving only one terminal event is returned when the agent handles its own error.

- [ ] **Step 7: Run the full runtime slice**

Run: `.venv/bin/pytest tests/test_evidence.py tests/test_semantic_gate.py tests/test_answer_gate.py tests/test_deadline.py tests/test_sessions.py tests/test_agent.py tests/test_app.py tests/test_tools.py tests/test_tracing.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the hard answer boundary**

```bash
git add src/agent.py src/app.py tests/test_agent.py tests/test_app.py
git commit -m "feat: gate final answers before emission"
```

---

### Task 9: Make eval results tri-state, evidence-complete, and provenance-bound

**Files:**
- Modify: `tests/test_eval_scoring.py`
- Create: `tests/test_contracts.py`
- Modify: `src/contracts.py`
- Modify: `evals/run_evals.py`
- Modify: `docs/decisions.md`

**Interfaces:**
- Add enums: `ScenarioSuite`, `ScenarioOutcome`, `CheckStatus`.
- Add check types: outcome, ordered tools, absent tools, exact returned value, forbidden answer content, per-turn assertion, latency ceiling.
- Add `EvalScenario.suite`, `outcome`, and `tags` with backward-compatible defaults.
- Add `CheckResult.status`; retain `passed` and set it false for `unverified`.
- Add `EvalRun.provenance` with Git SHA, dirty state, scenario hash, scorer version, model IDs, prompt hash, and snapshot fingerprint.

- [ ] **Step 1: Write failing backward-compatibility and tri-state tests**

Load at least one committed historical result through the new Pydantic models. Assert missing new fields receive defaults. Add arithmetic tests proving `unverified` is excluded from numerator and denominator, blocked coverage is excluded, and any unverified core scenario prevents a green core badge.

- [ ] **Step 2: Write failing observer-scoring tests**

Construct `TurnRecord` fixtures directly. Prove the scorer:

- examines every turn in a multi-turn scenario;
- uses complete query rows, not `TOOL_END.first_row`;
- requires exact returned values, expected lineage, successful runtime validation, and ordered tools for answer outcomes;
- distinguishes guardrail refusal from model self-refusal;
- marks missing observer evidence `unverified`, never pass;
- rejects forbidden prose and latency violations.

- [ ] **Step 3: Run and verify RED**

Run: `.venv/bin/pytest tests/test_contracts.py tests/test_eval_scoring.py -q`

Expected: FAIL because current contracts are boolean-only and the runner reads bounded SSE summaries.

- [ ] **Step 4: Add the approved eval-only contract deviation**

Make changes additive and defaulted. Keep `CheckResult.passed` for historical UI compatibility:

```python
class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIED = "unverified"

class CheckResult(BaseModel):
    check: Check
    passed: bool
    status: CheckStatus = CheckStatus.PASS
    observed: str | None = None
```

Use a model validator or construction helper to enforce `status == UNVERIFIED` implies `passed is False`.

- [ ] **Step 5: Replace SSE-summary observation with the internal turn observer**

Pass a collector to `agent_turn` for each turn. Keep per-turn records separately and derive scenario-wide assertions only where declared. Remove arithmetic inference and the `has_unseen_rows` green path. A missing record or incomplete row evidence becomes `unverified`.

- [ ] **Step 6: Compute and store provenance before running scenarios**

Hash canonical bytes for the scenario registry, scorer source, system prompt, and snapshot metadata. Read both model IDs from `src/model_config.py`. Record full Git SHA and dirty state. Refuse to present two runs as comparable when these identifiers differ.

- [ ] **Step 7: Record the decision**

Add D-024, or the next free ID under the global collision rule, explaining why additive eval-contract changes are permitted while runtime contracts remain frozen. Name the rejected alternative: keeping a boolean pass field would continue conflating failure with absence of evidence.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_contracts.py tests/test_eval_scoring.py -q`

Expected: PASS.

- [ ] **Step 9: Commit eval evidence and contracts**

```bash
git add src/contracts.py evals/run_evals.py tests/test_contracts.py tests/test_eval_scoring.py docs/decisions.md
git commit -m "feat: make eval evidence tri-state and reproducible"
```

---

### Task 10: Curate the 12-core suite and regression overlay

**Files:**
- Create: `tests/test_scenarios.py`
- Modify: `evals/scenarios.py`
- Modify: `evals/README.md`

**Interfaces:**
- Core IDs: `DF-05`, `DF-02`, `CMP-01`, `MT-01`, `MT-03`, `AMB-01`, `AMB-03`, `PM-02`, `PM-03`, `UN-01`, `OT-01`, `INJ-02`.
- Regression IDs: `UN-08`, `PM-08`.
- Historical only: `DF-01`, `AMB-02`.
- Conflicting-source scenarios remain blocked and outside all denominators.

- [ ] **Step 1: Write failing registry-structure tests**

Assert exact core and regression ID sets, unique IDs, suite/outcome/tags on every scored scenario, and strong outcome-specific checks. Every answer outcome must require a nonempty answer, successful SQL, variable lineage, geography lineage, exact returned values, and runtime answer validation. Clarify/refuse outcomes must forbid SQL.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/test_scenarios.py -q`

Expected: FAIL because the current 14-row registry has weak checks and no suite/outcome metadata.

- [ ] **Step 3: Rewrite the registry without deleting historical evidence**

Keep old result JSON untouched. Replace only the executable registry. Preserve `DF-01` and `AMB-02` in a named historical collection that the runner does not score. Represent blocked conflicting-source coverage explicitly in metadata or documentation, never as a passing or pending result.

- [ ] **Step 4: Gate the Cook County oracle on explicit approval**

Before finalizing `DF-02`, ask Brian for permission to execute one reviewed direct Snowflake query through `scripts/sf_query.py`. If approved, record the SQL, `B11001e1`, Cook County `geo_id`, exact value, and verification date in scenario notes. If not approved, keep `DF-02` `unverified` and prevent a green core badge. Never guess the literal.

- [ ] **Step 5: Document automated versus human evidence**

Update `evals/README.md` with suite arithmetic, unverified semantics, provenance compatibility, and the manual review artifact schema:

```json
{
  "scenario_id": "DF-05",
  "answer_sha256": "7a1f73f0c328b12042d811a4e374cf0388b4af676d4d8cae21d72a43ae0a8231",
  "reviewer": "Brian",
  "reviewed_at": "2026-08-10T20:15:00Z",
  "labels": {
    "correctness": true,
    "directness": true,
    "caveat_quality": true,
    "actionability": true
  },
  "critique": "Direct and grounded, with the ACS vintage stated once."
}
```

Do not create fake review rows before an approved live run exists.

- [ ] **Step 6: Run registry and scorer tests**

Run: `.venv/bin/pytest tests/test_scenarios.py tests/test_eval_scoring.py tests/test_app.py -k 'eval or scenario' -q`

Expected: PASS. If the oracle is unapproved, the tests assert its explicit unverified state rather than a fabricated expected value.

- [ ] **Step 7: Commit the curated suite**

```bash
git add evals/scenarios.py evals/README.md tests/test_scenarios.py
git commit -m "test: curate core and regression eval suites"
```

---

### Task 11: Make readiness and deployment verification truthful

**Files:**
- Modify: `tests/test_health.py`
- Modify: `tests/test_app.py`
- Create: `tests/test_deploy.py`
- Modify: `src/health.py`
- Modify: `src/app.py`
- Modify: `src/tools.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `deploy.sh`

**Interfaces:**
- `GET /livez`: process liveness only.
- `GET /readyz`: validated snapshot and last-known Snowflake query-path state.
- `GET /api/health`: backward-compatible summary from the same state.
- `GET /api/version`: app version, eval Git SHA, scenario hash, snapshot timestamp.
- Readiness endpoints never contact Snowflake.

- [ ] **Step 1: Write failing health-state tests**

Cover valid snapshot, missing snapshot, corrupt SQLite file, missing required tables/metadata, startup Snowflake success/failure, query-path success/failure updates, and observation timestamps. Assert readiness requires both validated snapshot and last-known Snowflake success. Assert repeated endpoint calls never invoke `_sf_connect`.

- [ ] **Step 2: Write failing endpoint and deploy-script tests**

Assert `/livez` stays live when dependencies are unready, `/readyz` returns `ready: false` with causes, and `/api/version` reads immutable environment/build metadata. In `tests/test_deploy.py`, inspect or invoke a factored Python readiness predicate and prove deployment fails on HTTP-only success with `ready=false` or an app-version mismatch.

- [ ] **Step 3: Run and verify RED**

Run: `.venv/bin/pytest tests/test_health.py tests/test_app.py tests/test_deploy.py -q`

Expected: FAIL because health currently trusts file existence, uses boot-only state, and deploy checks only HTTP reachability.

- [ ] **Step 4: Implement validated, cached readiness state**

Validate the snapshot by opening it read-only and checking required schema and metadata. Store Snowflake readiness as last-known state plus ISO observation timestamp. Initialize it with the bounded startup probe and update it from the only request-time Snowflake path in `run_census_sql`. Update success after a completed query, and update failure on connection or execution errors. The update functions must never raise or change the tool result. Health reads never probe live.

- [ ] **Step 5: Inject and expose immutable build identity**

Add `APP_VERSION` to `.env.example`. Pass the Git SHA into the Docker build as `ARG APP_VERSION`, then `ENV APP_VERSION=$APP_VERSION`. Have deploy export the expected SHA before `docker compose up -d --build app`. `/api/version` also reads eval provenance from `latest.json` defensively and the validated snapshot timestamp.

- [ ] **Step 6: Verify readiness JSON and expected version in deploy**

Poll `/readyz` and `/api/version`. Succeed only when readiness is true and returned `app_version` equals the SHA captured before build. Keep the 60-second outer deployment wait. Do not run the actual deploy in this task.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_health.py tests/test_app.py tests/test_deploy.py tests/test_tools.py -q`

Expected: PASS.

- [ ] **Step 8: Commit readiness and version truth**

```bash
git add src/health.py src/app.py src/tools.py Dockerfile docker-compose.yml .env.example deploy.sh tests/test_health.py tests/test_app.py tests/test_deploy.py
git commit -m "feat: verify readiness and deployed version"
```

---

### Task 12: Consolidate the reviewer UI into four truthful tabs

**Files:**
- Create: `tests/test_static_ui.py`
- Modify: `tests/test_app.py`
- Modify: `static/index.html`
- Modify: `src/app.py`

**Interfaces:**
- Visible tab order: Chat, Evidence, Evals, Trust Rules.
- Evidence consumes `/api/trace-sessions` and `/api/traces` through one selected-session and selected-turn state path.
- Evals consumes `/api/evals` plus `/api/version`.
- Trust Rules is static client-side data, adds no endpoint, and contains all 72 approved IDs.

- [ ] **Step 1: Write failing deterministic HTML contract tests**

Read `static/index.html` as text and assert exactly four visible tab labels in the required order, with no visible `Turn Detail`, `Trace Logging`, or `Data Source` labels. Extract trust-rule objects and assert:

- exactly 72 unique IDs;
- category prefixes `SRC`, `GEO`, `SQL`, `VAL`, `STAT`, `ANS`, and `OPS`;
- one recognized status, behavior statement, example, enforcement location, and evidence reference per rule;
- all five exact status labels are supported.

Also assert the Guided Review contains the six approved steps and loads prompts without dispatching `sendMessage()` automatically.

- [ ] **Step 2: Write failing eval-provenance endpoint/UI tests**

Assert `/api/evals` returns separate core, regression, failed, unverified, and blocked summaries plus provenance compatibility. Assert the UI contains a prominent SHA-mismatch state and cannot label an incompatible or unverified core run green.

- [ ] **Step 3: Run and verify RED**

Run: `.venv/bin/pytest tests/test_static_ui.py tests/test_app.py -k 'tab or evidence or trust or guided or provenance or eval' -q`

Expected: FAIL because five overlapping tabs and two trace render paths still exist.

- [ ] **Step 4: Replace navigation and merge technical views**

Use exactly four buttons in the requested order. Replace both former trace panels with one Evidence panel containing:

- history picker and refresh;
- selected-turn summary with outcome, elapsed time, model rounds, tool count, answer-validation status, repair count, and terminal reason;
- one ordered timeline for guardrail, model, tool, semantic gate, answer validation, repair, and terminal spans;
- expandable sanitized details.

Delete the duplicate `flow` and `trace` state/render paths. One selected session, one selected turn, and one timeline renderer own the view. When Evidence opens, select the newest completed Chat turn when available. Keep an honest empty state.

- [ ] **Step 5: Add Guided Review to Chat**

Use a native `<details>` card. Each step labels the requirement proved and copies its prompt into the existing input. The card never submits automatically and never starts a new session, so the multi-turn steps share current context.

- [ ] **Step 6: Replace Data Source with the complete Trust Rules catalog**

Retain the source inventory at the top. Encode the approved 72 rules from the spec as static structured objects, then render native `<details>` sections and a status filter. Use the exact status semantics from the spec. Do not claim deferred or prompt-only rules are hard enforced.

- [ ] **Step 7: Split eval capability from reliability and display provenance**

Render core and regression scores separately. List failed, unverified, and blocked coverage. Fetch `/api/version`, compare app SHA with eval SHA, and display mismatch prominently. An unverified core scenario or provenance mismatch must suppress the green core badge.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_static_ui.py tests/test_app.py -q`

Expected: PASS.

- [ ] **Step 9: Perform local browser verification**

Start `uvicorn src.app:app --reload`, open the app, and verify desktop plus narrow width. Confirm tab order, no duplicate trace view, Guided Review prompt loading, Evidence newest-turn selection, rule filtering, eval mismatch rendering, and no horizontal nav overflow caused by retired tabs.

- [ ] **Step 10: Commit the reviewer surface**

```bash
git add static/index.html src/app.py tests/test_static_ui.py tests/test_app.py
git commit -m "feat: consolidate the reviewer experience"
```

---

### Task 13: Sweep documentation and generated references for current truth

**Files:**
- Modify: `README.md`
- Modify: `docs/reflection.md`
- Modify: `docs/01-architecture.md`
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/decisions.md`
- Modify: `evals/README.md`
- Modify generated files selected by `make docs`

**Interfaces:**
- README opens with the five-minute review path and a rubric table with `claim`, `evidence`, and `known limit`.
- Historical pre-code documents retain their original claims with targeted supersession annotations.

- [ ] **Step 1: Update reviewer-facing truth**

Document the one-sentence request flow, Guided Review path, SQL trust boundary, runtime evidence and answer gates, four tabs, current data scope, tri-state eval interpretation, provenance, readiness semantics, and approval-gated live verification. State once that full aggregation validity, universe enforcement, 2019/Decennial data, tenancy, rate limits, and calibrated prose judging remain deferred.

- [ ] **Step 2: Correct stale current-state claims**

Search and fix stale references to:

- in-memory-only traces;
- Langfuse as shipped;
- prompt caching as shipped;
- hard streaming watchdog claims;
- Snowflake contacted exactly once rather than through one request-time code path;
- final grounding as prompt-only after the new runtime gate lands;
- Turn Detail, Trace Logging, and Data Source as current tabs.

Run: `rg -n 'in.memory|Langfuse|prompt cach|exactly once|watchdog|Turn Detail|Trace Logging|Data Source|five tabs|5 tabs' README.md AGENTS.md docs src static/index.html`

Expected: remaining matches are either historical quotations, superseded annotations, or test fixtures with clear context.

- [ ] **Step 3: Record architecture and scope decisions**

Add concise decision entries for the evidence/answer trust boundary, buffered answer tradeoff, four-tab consolidation, and last-known readiness semantics when they are not already covered by the approved D-024 entry. Name rejected alternatives and accepted costs. Update the repo map for the four new modules.

- [ ] **Step 4: Update the changelog and generated references**

Add `## [2026-08-10]` entries under Added, Changed, and Removed as appropriate. Run `make docs`, inspect the diff, and keep only expected ID-reference updates.

- [ ] **Step 5: Run documentation and ID-reference tests**

Run: `.venv/bin/pytest tests/test_id_reference.py tests/test_static_ui.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the truth sweep**

```bash
git add README.md docs/reflection.md docs/01-architecture.md AGENTS.md CHANGELOG.md docs/decisions.md evals/README.md
git commit -m "docs: align reviewer story with runtime truth"
```

After `make docs`, add any changed target from its fixed set: `README.md`, `docs/reflection.md`, `evals/README.md`, `docs/decisions.md`, and `CHANGELOG.md`. Never stage unrelated pre-existing changes.

---

### Task 14: Run release gates without fabricating live evidence

**Files:**
- Modify only after approved live runs: `evals/results/<timestamp>.json`, `evals/results/latest.json`
- Create only after human review: `evals/reviews/<run-stamp>.json`
- Modify if verification reveals a defect: the smallest owning source and test files

**Interfaces:**
- Offline completion is distinct from live-eval completion.
- Paid calls and deployment remain separately approval-gated.

- [ ] **Step 1: Run the complete offline suite**

Run: `.venv/bin/pytest -q`

Expected: all tests PASS. Record the exact count and any warnings. Do not describe the sprint as complete from focused tests alone.

- [ ] **Step 2: Run static repository checks**

Run:

```bash
git diff --check
rg -n 'TODO|TBD|PLACEHOLDER|NotImplementedError' src tests evals static README.md docs
git status --short
```

Expected: no new placeholders, whitespace errors, leaked result rows, or accidentally staged unrelated files. Existing intentional interface stubs in `src/contracts.py` must be identified rather than blindly removed.

- [ ] **Step 3: Invoke the required code-reviewer subagent**

Provide the approved spec, this plan, task description, and exact changed-file list. If verdict is FAIL, resolve every BLOCKING issue test-first and rerun the full offline suite.

- [ ] **Step 4: Ask for paid oracle and eval approval**

Request explicit permission for:

1. the one Cook County oracle query if not already approved;
2. one complete core eval run;
3. three regression-overlay repetitions.

If approval is declined or unavailable, stop with live-eval and human-review criteria visibly blocked. Do not write a synthetic green artifact.

- [ ] **Step 5: If approved, run the live measurements**

Run the reviewed oracle first, finalize `DF-02`, rerun offline tests, then execute the harness commands documented by the implemented CLI. The expected shape is:

```bash
make eval
.venv/bin/python -m evals.run_evals --suite regression --repeat 3
```

If the implemented CLI uses different exact flags, use `--help`, update `evals/README.md`, and run the documented form. Never overwrite `latest.json` with a filtered or regression-only result.

- [ ] **Step 6: Human-review all 12 core answers**

Hash each answer, record reviewer, timestamp, four binary labels, and critique in `evals/reviews/<run-stamp>.json`. This artifact is calibration data and does not modify automated pass rates.

- [ ] **Step 7: Re-run offline tests after committing live artifacts**

Run: `.venv/bin/pytest -q`

Expected: PASS, including artifact-schema and ID-reference tests.

- [ ] **Step 8: Commit only real measurement artifacts**

```bash
git add evals/results evals/reviews
git commit -m "test: record assignment eval evidence"
```

Skip this commit entirely when paid calls or human review were not approved.

- [ ] **Step 9: Report completion boundary and request deployment separately**

Report offline suite count, code-review verdict, core score, regression reliability, unverified/blocked rows, provenance, and human-review status. Ask separately before `make deploy`. A deployment is successful only when `/readyz` reports ready and `/api/version` matches the expected Git SHA.

## Execution Checkpoints

- **Checkpoint A, runtime trust boundary:** Tasks 1 through 8. Do not claim runtime grounding before this checkpoint passes its full focused suite.
- **Checkpoint B, eval credibility:** Tasks 9 and 10. Do not display a green core badge with unverified evidence or incompatible provenance.
- **Checkpoint C, production truth:** Task 11. Do not call an HTTP response alone readiness.
- **Checkpoint D, reviewer surface:** Tasks 12 and 13. This is the approved cut boundary if the implementation day expires after the first three checkpoints.
- **Checkpoint E, measured release:** Task 14. Offline completion may be reported without live completion, but the distinction must remain explicit.
