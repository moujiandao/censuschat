# CensusChat Reviewer-Clarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify CensusChat into a four-tab reviewer-facing app with one clear request-flow story, presentation-safe query results, credible deterministic evals, and minimal cost-aware CI.

**Architecture:** Preserve the existing three-tool agent and one-file frontend. Put normalization inside the existing `run_census_sql` seam, split the 14 executed eval scenarios into deterministic regression and informational capability suites, and consolidate duplicated UI surfaces around the existing trace store. Keep SQL safety as the runtime trust boundary and describe model instructions and eval checks honestly as separate layers.

**Tech Stack:** Python 3.13, Pydantic, pytest, FastAPI, Anthropic SDK, sqlglot, Snowflake connector, SQLite, vanilla HTML/CSS/JavaScript, Server-Sent Events, GitHub Actions.

## Global Constraints

- Timebox the implementation to approximately eight hours.
- Preserve exactly three agent tools: `search_census_variables`, `resolve_geography`, and `run_census_sql`.
- Preserve `static/index.html` as one vanilla HTML file with no build step or npm dependency.
- Preserve the current Snowflake and local-SQLite topology.
- Keep user text out of SQL and route every request-time Snowflake query through `run_census_sql` and `validate_sql`.
- Make only additive, backward-compatible changes to eval models in `src/contracts.py`, with a decision record and historical-artifact tests.
- Use `EvalOutcome.PASS`, `EvalOutcome.FAIL`, and `EvalOutcome.INCONCLUSIVE`; do not introduce a competing status name.
- Regression inconclusive is blocking. Capability inconclusive is reported and remains in the denominator.
- Do not add an LLM judge.
- Do not implement final-answer buffering, repair, a complete evidence ledger, semantic SQL lineage, session redesign, readiness redesign, or new Census vintages.
- Do not claim that every final answer number is code-validated. The accurate claim is: SQL safety is code-enforced; answer grounding is model-instructed and checked on selected eval scenarios.
- Do not run paid evals or deploy without Brian's explicit approval immediately before the action.
- Use TDD for deterministic behavior. Run focused tests, then the complete offline suite before every commit.
- Invoke the code-reviewer after every task that changes code. Resolve every blocking finding before committing.
- Preserve `docs/solutions.html` unchanged.
- Preserve unrelated dirty-worktree changes and stage only the files named by the active task.
- Execute implementation in a clean feature worktree created from this branch through `superpowers:using-git-worktrees`; do not implement inside the current dirty worktree.

## File Responsibility Map

| File | Responsibility in this build |
|---|---|
| `src/contracts.py` | Backward-compatible normalization and eval types |
| `src/tools.py` | Sole Snowflake execution path and private result normalization |
| `evals/scenarios.py` | Canonical 6/8 suite membership and scenario checks |
| `evals/run_evals.py` | Deterministic scoring, tri-state outcomes, suite selection, CI artifacts |
| `src/app.py` | Historical eval annotation and existing trace endpoints |
| `static/index.html` | Chat, How It Works, Evidence, and Evals |
| `.github/workflows/ci.yml` | Required credential-free pytest workflow |
| `.github/workflows/live-evals.yml` | Manual protected paid regression workflow |
| `docs/schema-notes.md` | Technical source of truth for normalization |
| `evals/README.md` | Evaluation contract and operational commands |
| `README.md` | Reviewer tour and accurate architecture claims |
| `docs/decisions.md` | Additive contract and UI consolidation decisions |

## Canonical Suite Membership

```python
REGRESSION_IDS = frozenset({
    "DF-05",
    "MT-01",
    "AMB-01",
    "UN-01",
    "OT-01",
    "INJ-02",
})

CAPABILITY_IDS = frozenset({
    "DF-01",
    "CMP-01",
    "AMB-02",
    "PM-02",
    "PM-03",
    "AMB-03",
    "UN-08",
    "PM-08",
})
```

## Eight-Hour Budget

| Task | Budget |
|---|---:|
| 1. Normalize query results | 45 minutes |
| 2. Strengthen and partition evals | 2 hours |
| 3. Add CI-aware runner and workflows | 1 hour 30 minutes |
| 4. Consolidate the four-tab frontend | 2 hours 30 minutes |
| 5. Complete the documentation truth sweep | 45 minutes |
| 6. Verify the build and run approved evals | 30 minutes offline, paid run time separate |

---

### Task 1: Make Query Results Presentation-Safe

**Files:**

- Modify: `src/tools.py:12-31,184-214`
- Modify: `tests/test_tools.py:406-460`
- Modify: `docs/schema-notes.md:124-130`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes: `normalize_value(raw: Any, variable_id: str | None) -> CensusValue` from `src/contracts.py`.
- Produces: `_projection_variable_ids(sql: str) -> dict[str, str]`, private to `src/tools.py`.
- Produces: `_presentation_value(column: str, raw: Any, variable_ids: dict[str, str]) -> Any`, private to `src/tools.py`.
- Preserves: `run_census_sql(sql: str) -> QueryResult` and `QueryResult.rows: list[dict[str, Any]]`.
- Result contract: demographic SQL NULL becomes `"not reported"`; the documented `B19013e1` top-code becomes `"$250,000 or more"`; ordinary numbers and identifiers retain their original values.

Tradeoff: parse the already validated SQL a second time to map result aliases back to one source Census variable. This keeps normalization private to the data-access seam without widening the tool interface. The rejected alternative was exposing `CensusValue` wrappers to the model and every caller.

- [ ] **Step 1: Write failing end-to-end tool tests**

Add these cases to `tests/test_tools.py`, using the existing `FakeSqlConnection` and an allowlisted 2020 table:

```python
def test_run_census_sql_normalizes_null_demographic_value(monkeypatch):
    sql = 'SELECT "B01003e1" AS population FROM US_CENSUS.PUBLIC."2020_CBG_B01"'
    fake = FakeSqlConnection(columns=["POPULATION"], rows=[(None,)])
    monkeypatch.setattr(tools, "_connect", lambda **kwargs: fake)

    result = tools.run_census_sql(sql)

    assert result.rows == [{"POPULATION": "not reported"}]


def test_run_census_sql_renders_aliased_income_top_code(monkeypatch):
    sql = 'SELECT "B19013e1" AS income FROM US_CENSUS.PUBLIC."2020_CBG_B19"'
    fake = FakeSqlConnection(columns=["INCOME"], rows=[(250001.0,)])
    monkeypatch.setattr(tools, "_connect", lambda **kwargs: fake)

    result = tools.run_census_sql(sql)

    assert result.rows == [{"INCOME": "$250,000 or more"}]


def test_run_census_sql_preserves_ordinary_numbers_and_identifiers(monkeypatch):
    sql = (
        'SELECT CENSUS_BLOCK_GROUP, "B01003e1" AS population '
        'FROM US_CENSUS.PUBLIC."2020_CBG_B01"'
    )
    fake = FakeSqlConnection(
        columns=["CENSUS_BLOCK_GROUP", "POPULATION"],
        rows=[("060014001001", 581348)],
    )
    monkeypatch.setattr(tools, "_connect", lambda **kwargs: fake)

    result = tools.run_census_sql(sql)

    assert result.rows == [
        {"CENSUS_BLOCK_GROUP": "060014001001", "POPULATION": 581348}
    ]
```

- [ ] **Step 2: Run the new tests and confirm the current raw-row behavior fails**

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest \
  tests/test_tools.py::test_run_census_sql_normalizes_null_demographic_value \
  tests/test_tools.py::test_run_census_sql_renders_aliased_income_top_code \
  tests/test_tools.py::test_run_census_sql_preserves_ordinary_numbers_and_identifiers -q
```

Expected: the first two tests fail because `run_census_sql` currently returns `None` and `250001.0` unchanged; the preservation test passes or remains unchanged.

- [ ] **Step 3: Add the private projection and presentation helpers**

Add the installed sqlglot imports and use the sanitized SQL returned by the gate:

```python
from sqlglot import exp, parse_one

from src.contracts import normalize_value

_VARIABLE_ID_RE = re.compile(r"^[A-Z]+\d+[A-Z]\d+$", re.IGNORECASE)


def _projection_variable_ids(sql: str) -> dict[str, str]:
    tree = parse_one(sql, read="snowflake")
    result: dict[str, str] = {}
    for projection in tree.selects:
        variable_ids = {
            column.name
            for column in projection.find_all(exp.Column)
            if _VARIABLE_ID_RE.fullmatch(column.name)
        }
        if isinstance(projection, exp.Column) and _VARIABLE_ID_RE.fullmatch(projection.name):
            variable_ids.add(projection.name)
        if len(variable_ids) == 1:
            result[projection.alias_or_name.upper()] = variable_ids.pop()
    return result


def _presentation_value(
    column: str,
    raw: Any,
    variable_ids: dict[str, str],
) -> Any:
    variable_id = variable_ids.get(column.upper())
    if variable_id is None:
        return raw
    normalized = normalize_value(raw, variable_id)
    if normalized.suppressed:
        return "not reported"
    if normalized.top_coded:
        return "$250,000 or more"
    return raw
```

In `run_census_sql`, replace raw `dict(zip(...))` construction with:

```python
variable_ids = _projection_variable_ids(gate_result.sql)
row_dicts = [
    {
        column: _presentation_value(column, raw, variable_ids)
        for column, raw in zip(columns, row)
    }
    for row in rows
]
```

- [ ] **Step 4: Run normalization and tool tests**

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest \
  tests/test_normalize_value.py tests/test_tools.py tests/test_tool_summary.py -q
```

Expected: PASS. Confirm tool summaries and watchdog partial rows now contain presentation-safe values.

- [ ] **Step 5: Document only the verified rules**

Update `docs/schema-notes.md` to state:

```markdown
At request time, `run_census_sql` normalizes model-facing demographic cells.
SQL NULL becomes `not reported`. A direct or aliased `B19013e1` value of
250001 becomes `$250,000 or more`. Ordinary numeric cells and identifiers are
unchanged. This share uses SQL NULL for suppression; no numeric sentinel code
was observed or transformed.
```

Add a `CHANGELOG.md` entry describing normalization at the Snowflake result seam.

- [ ] **Step 6: Run the complete suite, request code review, and commit**

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest -q
```

Invoke the code-reviewer with Task 1 and the changed code files. Resolve all blocking findings. Then stage only:

```bash
git add src/tools.py tests/test_tools.py \
  docs/schema-notes.md CHANGELOG.md
git commit -m "fix: normalize census query results"
```

### Task 2: Partition the Evals and Eliminate Known False Greens

**Files:**

- Modify: `src/contracts.py:140-169,301-350`
- Modify: `evals/scenarios.py`
- Modify: `evals/run_evals.py:80-429`
- Modify: `tests/test_eval_scoring.py`
- Modify: `docs/decisions.md`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Produces: `EvalSuite(str, Enum)` with `REGRESSION` and `CAPABILITY`.
- Produces: `EvalOutcome(str, Enum)` with `PASS`, `FAIL`, and `INCONCLUSIVE`.
- Adds: `CheckType.ANSWER_REQUIRED` and `CheckType.NO_MEDIAN_AGGREGATION`.
- Adds: `EvalScenario.suite: EvalSuite` with a backward-compatible default.
- Adds: `CheckResult.outcome: EvalOutcome | None = None`; retains `passed`.
- Adds: `EvalResult.suite: EvalSuite | None = None` and `EvalResult.outcome: EvalOutcome | None = None`; retains `passed` and the existing executed/pending `status` field.
- Produces: `_check_result(check: Check, outcome: EvalOutcome, observed: str) -> CheckResult`.
- Produces: `_scenario_outcome(checks: list[CheckResult]) -> EvalOutcome`.

Tradeoff: keep `passed` beside the new outcome fields so every committed historical artifact still parses. The rejected alternative was rewriting prior JSON or overloading the existing executed/pending `status` field with a second meaning.

- [ ] **Step 1: Write failing contract and partition tests**

Add to `tests/test_eval_scoring.py`:

```python
def test_suite_partition_is_exact_and_complete():
    from evals.scenarios import GOLDEN_SCENARIOS
    from src.contracts import EvalSuite

    regression = {s.id for s in GOLDEN_SCENARIOS if s.suite == EvalSuite.REGRESSION}
    capability = {s.id for s in GOLDEN_SCENARIOS if s.suite == EvalSuite.CAPABILITY}

    assert regression == {"DF-05", "MT-01", "AMB-01", "UN-01", "OT-01", "INJ-02"}
    assert capability == {
        "DF-01", "CMP-01", "AMB-02", "PM-02",
        "PM-03", "AMB-03", "UN-08", "PM-08",
    }
    assert regression.isdisjoint(capability)
    assert len(regression | capability) == 14


def test_historical_eval_models_parse_without_new_fields():
    from src.contracts import CheckResult, EvalResult

    check = CheckResult.model_validate({
        "check": {"type": "no_unhandled_error", "expected": None},
        "passed": True,
        "observed": "terminal=done",
    })
    result = EvalResult.model_validate({
        "scenario_id": "DF-05",
        "category": "direct_fact",
        "passed": True,
        "checks": [check.model_dump()],
    })

    assert check.outcome is None
    assert result.suite is None
    assert result.outcome is None
```

- [ ] **Step 2: Write failing tri-state and stronger-grader tests**

Add focused cases:

```python
def test_answer_required_rejects_blank_text():
    check = Check(type=CheckType.ANSWER_REQUIRED)
    assert _score_check(check, _obs(answer="   ")).outcome == EvalOutcome.FAIL


def test_unseen_rows_make_grounding_inconclusive():
    obs = _grounding_obs("The largest is 999,111.", rows=[{"POP": 123456}], row_count=10)
    result = _grounding_check(obs)
    assert result.passed is False
    assert result.outcome == EvalOutcome.INCONCLUSIVE


def test_regression_inconclusive_is_not_a_pass():
    checks = [
        CheckResult(
            check=Check(type=CheckType.JUDGE_GROUNDEDNESS),
            passed=False,
            outcome=EvalOutcome.INCONCLUSIVE,
        )
    ]
    assert _scenario_outcome(checks) == EvalOutcome.INCONCLUSIVE


def test_median_aggregation_is_rejected():
    obs = _obs(tool_calls=[{
        "tool": "run_census_sql",
        "args": '{"sql":"SELECT AVG(\\"B19013e1\\") FROM t"}',
        "ok": True,
        "summary": {},
    }])
    check = Check(type=CheckType.NO_MEDIAN_AGGREGATION, expected="B19013e1")
    assert _score_check(check, obs).outcome == EvalOutcome.FAIL
```

Also change the existing `test_unverifiable_is_reported_as_inconclusive_not_as_a_failure` expectation from a passing boolean to the explicit inconclusive outcome.

- [ ] **Step 3: Run the focused tests and verify red**

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest tests/test_eval_scoring.py -q
```

Expected: FAIL because the enums, fields, checks, and outcome helpers do not exist and the current unseen-row branch reports `passed=True`.

- [ ] **Step 4: Add backward-compatible eval types**

Add to `src/contracts.py`:

```python
class EvalSuite(str, Enum):
    REGRESSION = "regression"
    CAPABILITY = "capability"


class EvalOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
```

Add enum members and fields exactly as declared in the Interfaces block. Default `EvalScenario.suite` to `EvalSuite.CAPABILITY`; every live scenario will set it explicitly in `evals/scenarios.py`.

- [ ] **Step 5: Centralize outcome construction and scenario aggregation**

Add to `evals/run_evals.py`:

```python
def _check_result(
    check: Check,
    outcome: EvalOutcome,
    observed: str,
) -> CheckResult:
    return CheckResult(
        check=check,
        passed=outcome == EvalOutcome.PASS,
        outcome=outcome,
        observed=observed,
    )


def _scenario_outcome(checks: list[CheckResult]) -> EvalOutcome:
    outcomes = {c.outcome or (EvalOutcome.PASS if c.passed else EvalOutcome.FAIL) for c in checks}
    if EvalOutcome.FAIL in outcomes:
        return EvalOutcome.FAIL
    if EvalOutcome.INCONCLUSIVE in outcomes:
        return EvalOutcome.INCONCLUSIVE
    return EvalOutcome.PASS
```

Route every `_score_check` and `_grounding_check` return through `_check_result`. Change unseen-row grounding to `EvalOutcome.INCONCLUSIVE` with `passed=False`.

Implement `ANSWER_REQUIRED` as `bool(obs.final_answer.strip())`. Strengthen `EXPECT_CLARIFYING_QUESTION` to reject any attempted `run_census_sql`, whether or not it succeeded.

Implement `NO_MEDIAN_AGGREGATION` by parsing every recorded SQL argument with sqlglot and failing when the expected median variable appears beneath `SUM` or `AVG`:

```python
def _aggregates_variable(sql: str, variable_id: str) -> bool:
    tree = parse_one(sql, read="snowflake")
    for aggregate in tree.find_all(exp.Sum, exp.Avg):
        if any(c.name.lower() == variable_id.lower() for c in aggregate.find_all(exp.Column)):
            return True
    return False
```

A malformed recorded SQL argument returns fail with an explicit scorer reason; it does not crash the run.

- [ ] **Step 6: Assign suites and strengthen the 14 declarations**

In `evals/scenarios.py`:

- add `suite=EvalSuite.REGRESSION` to the six canonical regression scenarios;
- add `suite=EvalSuite.CAPABILITY` to the remaining eight;
- add `ANSWER_REQUIRED` to every answerable or refusal scenario;
- keep `DF-05` variable `B01003e1`, geography `56`, and exact text `581,348`;
- strengthen `MT-01` with variable `B11012e1` and exact text `1,635,749`;
- keep `AMB-01` as a clarification with no SQL attempt;
- retain zero-tool refusal behavior for `UN-01`, `OT-01`, and `INJ-02`;
- add `NO_MEDIAN_AGGREGATION` with expected `B19013e1` to `PM-02` and `AMB-03`;
- strengthen `CMP-01` with `B01003e1` and the stable phrase `Travis County, TX has more` while it remains non-blocking capability evidence.

- [ ] **Step 7: Store the tri-state scenario result**

In `_run_all`:

```python
outcome = _scenario_outcome(check_results)
results.append(
    EvalResult(
        scenario_id=scenario.id,
        category=scenario.category,
        suite=scenario.suite,
        outcome=outcome,
        passed=outcome == EvalOutcome.PASS,
        checks=check_results,
        answer_final=obs.final_answer,
        elapsed_s=round(elapsed, 1),
    )
)
```

Keep `pass_rate = passes / all executed scenarios`; inconclusive remains in the denominator.

- [ ] **Step 8: Record the contract decision and verify**

Add `D-024` to `docs/decisions.md`, explaining the additive fields, historical compatibility, and why `status` was not overloaded. Add the change to `CHANGELOG.md`.

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest \
  tests/test_eval_scoring.py tests/test_app.py tests/test_id_reference.py -q
make docs
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest -q
```

Invoke the code-reviewer with Task 2 and all changed code files. Resolve blocking findings. Stage only the task files and any generated ID-reference changes, then commit:

```bash
git add src/contracts.py evals/scenarios.py evals/run_evals.py \
  tests/test_eval_scoring.py docs/decisions.md CHANGELOG.md
git add README.md evals/README.md docs/reflection.md
git commit -m "feat: make eval outcomes credible"
```

### Task 3: Add a CI-Aware Runner and Two Minimal Workflows

**Files:**

- Modify: `evals/run_evals.py:407-end`
- Modify: `tests/test_eval_scoring.py`
- Create: `tests/test_workflows.py`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/live-evals.yml`
- Modify: `Makefile`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Produces: `_parse_args(argv: list[str] | None = None) -> argparse.Namespace`.
- Produces: `_select_suite(scenarios: list[EvalScenario], suite: EvalSuite | None) -> list[EvalScenario]`.
- Produces: `_ci_payload(suite: str, runs: list[EvalRun]) -> dict`.
- Produces: `_ci_exit_code(suite: str, runs: list[EvalRun]) -> int`.
- CLI: `--suite regression|capability|all`, existing `--only`, existing `--repeat N`, `--ci`, and `--output PATH`.
- Benchmark mode: plain `make eval` keeps writing timestamped committed-run candidates and `latest.json`.
- CI mode: requires `--output`, writes one artifact containing all trials, never touches `evals/results/`, and exits nonzero when a regression result is fail or inconclusive.

- [ ] **Step 1: Write failing parser, artifact, and exit-semantics tests**

Add tests using `tmp_path` and monkeypatched `_run_all`. Define this local helper in the test module:

```python
def _run_with_outcome(outcome: EvalOutcome) -> EvalRun:
    passed = outcome == EvalOutcome.PASS
    result = EvalResult(
        scenario_id="DF-05",
        category=ScenarioCategory.DIRECT_FACT,
        suite=EvalSuite.REGRESSION,
        outcome=outcome,
        passed=passed,
        checks=[],
    )
    return EvalRun(
        run_at=datetime.now(timezone.utc),
        git_sha="abc123",
        results=[result],
        pass_rate=1.0 if passed else 0.0,
    )
```

Then add:

```python
def test_ci_requires_explicit_output():
    with pytest.raises(SystemExit):
        _parse_args(["--suite", "regression", "--ci"])


def test_ci_payload_keeps_both_trials_and_provenance():
    run_one = _run_with_outcome(EvalOutcome.PASS)
    run_two = _run_with_outcome(EvalOutcome.PASS)
    payload = _ci_payload("regression", [run_one, run_two])
    assert payload["suite"] == "regression"
    assert payload["repeat"] == 2
    assert len(payload["runs"]) == 2
    assert payload["models"] == {
        "agent": AGENT_MODEL,
        "classifier": CLASSIFIER_MODEL,
    }


def test_regression_pass_power_k_requires_every_trial_to_pass():
    passing_run = _run_with_outcome(EvalOutcome.PASS)
    inconclusive_run = _run_with_outcome(EvalOutcome.INCONCLUSIVE)
    failing_run = _run_with_outcome(EvalOutcome.FAIL)
    assert _ci_exit_code("regression", [passing_run, passing_run]) == 0
    assert _ci_exit_code("regression", [passing_run, inconclusive_run]) == 1
    assert _ci_exit_code("regression", [passing_run, failing_run]) == 1
```

Add a filesystem test that snapshots the bytes of a temporary `latest.json`, runs CI output writing, and proves the bytes are unchanged.

- [ ] **Step 2: Refactor argument parsing without changing benchmark behavior**

Move parser creation into `_parse_args`. Validate:

```python
if args.repeat < 1:
    parser.error("--repeat must be at least 1")
if args.ci and not args.output:
    parser.error("--ci requires --output PATH")
if args.output and not args.ci:
    parser.error("--output is only valid with --ci")
```

Suite selection occurs before `--only`; `--only` intersects the selected suite and still rejects unknown IDs.

- [ ] **Step 3: Implement one explicit CI artifact**

Use the model IDs from `src/model_config.py` and write atomically:

```python
def _ci_payload(suite: str, runs: list[EvalRun]) -> dict:
    return {
        "mode": "ci",
        "suite": suite,
        "repeat": len(runs),
        "models": {"agent": AGENT_MODEL, "classifier": CLASSIFIER_MODEL},
        "runs": [json.loads(run.model_dump_json()) for run in runs],
    }


def _write_ci(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)
```

For `--ci`, collect all repeated runs, write once, and return `_ci_exit_code`. Capability completion returns zero even with capability failures; runner or credential failure remains a nonzero infrastructure error.

- [ ] **Step 4: Write workflow contract tests before workflow files**

Create `tests/test_workflows.py`:

```python
from pathlib import Path


def test_offline_ci_is_credential_free_and_pr_safe():
    text = Path(".github/workflows/ci.yml").read_text()
    assert "pull_request:" in text
    assert "push:" in text
    assert "python -m pytest -q" in text
    assert "pull_request_target" not in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "SNOWFLAKE_" not in text


def test_live_evals_are_manual_protected_and_upload_on_failure():
    text = Path(".github/workflows/live-evals.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "environment: live-evals" in text
    assert "--suite regression --ci --repeat 2" in text
    assert "build_snapshot" in text
    assert "SNOWFLAKE_PRIVATE_KEY_B64" in text
    assert "if: always()" in text
    assert "pull_request:" not in text
    assert "pull_request_target" not in text
```

Run these tests and confirm they fail because the workflows do not exist.

- [ ] **Step 5: Create the offline workflow**

Create `.github/workflows/ci.yml` with this structure:

```yaml
name: Offline CI
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: offline-ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
      - run: python -m pip install -r requirements.txt
      - run: python -m pytest -q
```

- [ ] **Step 6: Create the manual live-eval workflow**

Create `.github/workflows/live-evals.yml` with `workflow_dispatch` only, least-privilege permissions, concurrency cancellation, `timeout-minutes: 20`, and `environment: live-evals`. Materialize the protected base64-encoded private key and build the local snapshot before the run:

```yaml
- name: Write Snowflake private key
  env:
    SNOWFLAKE_PRIVATE_KEY_B64: ${{ secrets.SNOWFLAKE_PRIVATE_KEY_B64 }}
  run: echo "$SNOWFLAKE_PRIVATE_KEY_B64" | base64 --decode > "${{ runner.temp }}/snowflake-key.p8"

- name: Build local metadata snapshot
  env:
    SNOWFLAKE_ACCOUNT: ${{ secrets.SNOWFLAKE_ACCOUNT }}
    SNOWFLAKE_USER: ${{ secrets.SNOWFLAKE_USER }}
    SNOWFLAKE_PRIVATE_KEY_PATH: ${{ runner.temp }}/snowflake-key.p8
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE: ${{ secrets.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE }}
    SNOWFLAKE_WAREHOUSE: ${{ secrets.SNOWFLAKE_WAREHOUSE }}
    SNOWFLAKE_ROLE: ${{ secrets.SNOWFLAKE_ROLE }}
    SNOWFLAKE_DATABASE: ${{ secrets.SNOWFLAKE_DATABASE }}
    SNOWFLAKE_SCHEMA: ${{ secrets.SNOWFLAKE_SCHEMA }}
  run: python -c "from src.snapshot import build_snapshot; build_snapshot()"

- name: Run regression twice
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    SNOWFLAKE_ACCOUNT: ${{ secrets.SNOWFLAKE_ACCOUNT }}
    SNOWFLAKE_USER: ${{ secrets.SNOWFLAKE_USER }}
    SNOWFLAKE_PRIVATE_KEY_PATH: ${{ runner.temp }}/snowflake-key.p8
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE: ${{ secrets.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE }}
    SNOWFLAKE_WAREHOUSE: ${{ secrets.SNOWFLAKE_WAREHOUSE }}
    SNOWFLAKE_ROLE: ${{ secrets.SNOWFLAKE_ROLE }}
    SNOWFLAKE_DATABASE: ${{ secrets.SNOWFLAKE_DATABASE }}
    SNOWFLAKE_SCHEMA: ${{ secrets.SNOWFLAKE_SCHEMA }}
  run: python -m evals.run_evals --suite regression --ci --repeat 2 --output artifacts/regression.json

- name: Upload result
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: live-regression-${{ github.sha }}
    path: artifacts/regression.json
    if-no-files-found: warn
```

Do not add a schedule, pull-request trigger, or capability job.

- [ ] **Step 7: Verify benchmark compatibility and commit**

Keep `make eval` unchanged. Add a comment showing the manual CI-equivalent command, not a second Make target.

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest \
  tests/test_eval_scoring.py tests/test_workflows.py -q
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest -q
```

Invoke the code-reviewer, resolve blocking findings, then stage only Task 3 files and commit:

```bash
git add evals/run_evals.py tests/test_eval_scoring.py tests/test_workflows.py \
  .github/workflows/ci.yml .github/workflows/live-evals.yml Makefile CHANGELOG.md
git commit -m "ci: add offline and manual eval workflows"
```

### Task 4: Consolidate the App Into Four Reviewer-Facing Tabs

**Files:**

- Modify: `static/index.html`
- Modify: `src/app.py:90-180,230-260`
- Create: `tests/test_frontend.py`
- Modify: `tests/test_app.py:180-360`
- Delete: `docs/flow-diagram.html`
- Modify: `docs/decisions.md`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Preserves: `GET /api/traces?session_id=...` and `GET /api/trace-sessions`.
- Extends: `_scenario_index() -> dict[str, dict]` to include suite metadata.
- Extends: `_annotate(...)` to derive suite and outcome for historical artifacts.
- Produces four exact tab IDs: `chat`, `how`, `evidence`, `evals`.
- Produces `EXAMPLE_QUESTIONS`, four Chat buttons with the approved label.
- Evidence uses the existing trace endpoint and one session picker.

Tradeoff: retain the existing single large frontend file because the no-build invariant is more important than splitting it during a deadline build. Delete duplicate render paths inside that file so one trace has one reviewer-facing representation.

- [ ] **Step 1: Write failing static frontend tests**

Create `tests/test_frontend.py`:

```python
from pathlib import Path


def _html() -> str:
    return Path("static/index.html").read_text()


def test_exact_reviewer_tab_order():
    html = _html()
    labels = ["Chat", "How It Works", "Evidence", "Evals"]
    positions = [html.index(f">{label}</button>") for label in labels]
    assert positions == sorted(positions)
    assert html.count('data-tab="') == 4


def test_legacy_top_level_surfaces_are_removed():
    html = _html()
    assert ">Turn Detail</button>" not in html
    assert ">Trace Logging</button>" not in html
    assert ">Data Source</button>" not in html


def test_how_it_works_names_the_three_protection_layers():
    html = _html()
    assert "Code protections" in html
    assert "Model instructions" in html
    assert "Evaluation checks" in html


def test_chat_has_four_example_questions():
    html = _html()
    assert "Example questions" in html
    assert html.count('class="example-question"') == 4


def test_evidence_defaults_to_curated_trace_with_optional_raw_json():
    html = _html()
    assert 'id="evidence-content"' in html
    assert "Raw trace JSON" in html
    assert "<details" in html


def test_empty_and_failed_technical_views_have_explicit_copy():
    html = _html()
    assert "No turns recorded for this session yet." in html
    assert "Couldn't load evidence." in html
    assert "No eval runs recorded yet." in html
    assert "Couldn't load eval results." in html
```

Run `python -m pytest tests/test_frontend.py -q` and confirm the old five-tab markup fails.

- [ ] **Step 2: Add historical suite and outcome annotation tests**

Extend `tests/test_app.py` with a historical artifact lacking new fields:

```python
def test_evals_endpoint_derives_suite_and_outcome_for_historical_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("src.app._EVALS_RESULTS_DIR", tmp_path)
    run = _run_with(["DF-05", "PM-08"])
    run["results"][1]["passed"] = False
    (tmp_path / "latest.json").write_text(json.dumps(run))

    rows = client.get("/api/evals").json()["latest"]["results"]

    assert [(r["suite"], r["outcome"]) for r in rows] == [
        ("regression", "pass"),
        ("capability", "fail"),
    ]
```

- [ ] **Step 3: Annotate eval artifacts at the app seam**

Extend `_scenario_index`:

```python
return {
    s.id: {
        "turns": list(s.turns),
        "notes": s.notes,
        "suite": s.suite.value,
    }
    for s in GOLDEN_SCENARIOS
}
```

In `_annotate`, preserve explicit new fields and derive historical values:

```python
result["suite"] = result.get("suite") or (meta["suite"] if meta else "capability")
result["outcome"] = result.get("outcome") or (
    "pass" if result.get("passed") else "fail"
)
```

Change `_history` scenario values from booleans to explicit outcome strings, deriving old rows the same way.

- [ ] **Step 4: Replace navigation and add Chat examples**

Use exactly:

```html
<nav class="tabs">
  <button type="button" data-tab="chat" class="active">Chat</button>
  <button type="button" data-tab="how">How It Works</button>
  <button type="button" data-tab="evidence">Evidence</button>
  <button type="button" data-tab="evals">Evals</button>
</nav>
```

Place these four `example-question` buttons above the chat log:

```javascript
const EXAMPLE_QUESTIONS = [
  { q: "Population of Harris County, Texas?", label: "Start with a factual question" },
  { q: "What about households?", label: "Follow up after the first question" },
  { q: "How many people live in Washington County?", label: "See ambiguity handling" },
  { q: "How many people will live in Texas in 2050?", label: "See an unsupported request" },
];
```

Clicking an example populates and focuses the input. It does not auto-submit or create a separate mode.

- [ ] **Step 5: Build How It Works from existing content**

Create one static panel containing:

```text
Question → Guardrail → Local discovery → SQL gate → Snowflake → Normalize → Answer → Evidence
```

Include the three tools, local SQLite versus Snowflake split, verified normalization table, data source/vintage summary, and the exact three labels `Code protections`, `Model instructions`, and `Evaluation checks`.

Under Model instructions, use the approved claim:

```text
The model is instructed to ground answer numbers in this turn's query results.
The serving path does not independently validate every final answer number.
```

Under Evaluation checks, explain that six stable scenarios are regression and eight broader scenarios are capability. Do not embed a second full eval-flow diagram.

- [ ] **Step 6: Consolidate Evidence around the current trace store**

Rename the existing curated Turn Detail renderer to Evidence terminology and retain its ordered step cards. Remove the separate trace table renderer. Add raw JSON inside each stored turn:

```javascript
const raw = document.createElement("details");
raw.className = "raw-trace";
raw.innerHTML = "<summary>Raw trace JSON</summary><pre>" +
  escapeHtml(JSON.stringify(trace, null, 2)) + "</pre>";
wrap.appendChild(raw);
```

Keep one history selector, one refresh button, and one `/api/traces` request. A turn with zero tool calls still shows guardrail/model spans and terminal timing rather than an empty state.

- [ ] **Step 7: Simplify Evals rendering**

Move example questions out of Evals. Render two sections in this order:

```javascript
const suiteOrder = ["regression", "capability"];
const outcomeOrder = ["pass", "fail", "inconclusive"];
```

For each suite, show counts over every row, then the existing question, checks, answer preview, and notes. Use an amber badge for inconclusive. Show run timestamp and model IDs when present. Add:

```text
CI logs and downloadable live-run artifacts are kept in GitHub Actions.
This page shows the latest committed benchmark.
```

Do not add workflow buttons or Actions history.

- [ ] **Step 8: Delete redundant surfaces and update internal references**

Remove the old Turn Detail, Trace Logging, and Data Source panel markup, CSS used only by those deleted renderers, duplicate session selectors, duplicate refresh paths, and comments that tell users to open removed tabs.

Delete `docs/flow-diagram.html` after its useful request-flow content is represented in How It Works. Do not edit or stage `docs/solutions.html`.

Add `D-025` to `docs/decisions.md`, describing the approved four tabs, deletion of duplicate trace renderers, preservation of the existing trace store, and removal of the standalone flow diagram.

- [ ] **Step 9: Verify, review, and commit**

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest \
  tests/test_frontend.py tests/test_app.py tests/test_tracing.py tests/test_agent.py -q
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest -q
```

Invoke the code-reviewer, resolve blocking findings, then stage only Task 4 files and commit:

```bash
git add static/index.html src/app.py tests/test_frontend.py tests/test_app.py \
  docs/flow-diagram.html docs/decisions.md CHANGELOG.md
git commit -m "feat: simplify the reviewer interface"
```

### Task 5: Make Documentation Match the Simplified System

**Files:**

- Modify: `README.md`
- Modify: `evals/README.md`
- Modify: `docs/reflection.md`
- Modify: `docs/01-architecture.md`
- Modify: `docs/plans/02-prd.md`
- Modify: `docs/decisions.md`
- Modify: `CLAUDE.md`
- Modify: `src/agent.py` comments and docstrings only
- Modify: `src/app.py` comments and docstrings only
- Modify: `CHANGELOG.md`
- Test: `tests/test_id_reference.py`

**Interfaces:**

- Produces one reviewer explanation shared across README and How It Works.
- Preserves historical pre-code documents by adding current-status annotations rather than rewriting original decisions.
- Preserves `docs/solutions.html` byte-for-byte.

- [ ] **Step 1: Add a documentation truth test for the reviewer-facing claims**

Add to `tests/test_id_reference.py` or a new focused documentation test:

```python
def test_readme_uses_current_tabs_and_grounding_claim():
    text = Path("README.md").read_text()
    assert "Chat, How It Works, Evidence, and Evals" in text
    assert "SQL safety is code-enforced" in text
    assert "Turn Detail tab" not in text
    assert "Trace Logging tab" not in text
```

Run the focused test and confirm it fails on current documentation.

- [ ] **Step 2: Rewrite the reviewer tour, not the whole README**

Make the first reviewer section explain:

```text
Question → local discovery → SQL gate → Snowflake → normalized result → answer → evidence
```

Describe the four tabs and the four Example questions. Retain startup, test, credential, and deployment commands that remain accurate.

Use these claims exactly once:

```text
SQL safety is code-enforced. Answer grounding is model-instructed and checked
on selected eval scenarios; this build does not independently validate every
final answer number at runtime.
```

Describe the watchdog as a soft deadline checked by current code, not an absolute interrupt guarantee.

- [ ] **Step 3: Replace `evals/README.md` with the current evaluation contract**

Document:

- exact regression and capability IDs;
- pass, fail, and inconclusive semantics;
- inconclusive remaining in the denominator;
- regression pass^k across two manual trials;
- plain `make eval` committed-benchmark behavior;
- manual `--ci --output` behavior;
- credential and paid-call requirements;
- red-row preservation;
- what each deterministic grader proves;
- why subjective prose remains human-reviewed;
- the condition for any future calibrated judge.

- [ ] **Step 4: Add targeted supersession and reflection updates**

In the architecture and PRD preambles, add a dated annotation pointing to the reviewer-clarity spec and current four-tab UI. Do not rewrite historical sections.

Update `docs/reflection.md` to state:

- traces persist in SQLite;
- Turn Detail and Trace Logging were consolidated into Evidence;
- Langfuse and prompt caching are not implemented;
- there is one Snowflake code path, potentially multiple calls;
- normalization is now wired at the result seam;
- the grounding and watchdog claims use the narrow approved language;
- deterministic regression and informational capability have different jobs.

- [ ] **Step 5: Update the project map to match D-025**

Confirm `D-025` accurately describes the shipped four-tab implementation. Amend only factual details discovered during implementation; do not change the approved scope.

In the clean implementation worktree, target-update the tracked `CLAUDE.md` rule 15, repo map, and current tracing description. Do not stage the current primary worktree's untracked `AGENTS.md` or symlink conversion. Update only stale comments/docstrings in `src/agent.py` and `src/app.py` that name removed tabs or overstate current behavior.

- [ ] **Step 6: Regenerate references, prove `docs/solutions.html` was not touched, and commit**

Before editing, record its blob hash:

```bash
git hash-object docs/solutions.html
```

After documentation changes, run:

```bash
make docs
git diff -- docs/solutions.html
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest \
  tests/test_id_reference.py tests/test_frontend.py -q
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest -q
```

Expected: `git diff -- docs/solutions.html` prints nothing and the full suite passes.

Invoke the code-reviewer because `src/agent.py` and `src/app.py` are code files even though this task changes only their comments. Resolve blocking findings. Stage only Task 5 files and commit:

```bash
git add README.md evals/README.md docs/reflection.md docs/01-architecture.md \
  docs/plans/02-prd.md docs/decisions.md CLAUDE.md src/agent.py src/app.py \
  CHANGELOG.md tests/test_id_reference.py
git commit -m "docs: align the reviewer story"
```

### Task 6: Verify the Deadline Build and Run Approved Live Evals

**Files:**

- No planned source changes.
- Generated, not committed: `tmp/regression-ci.json`.
- Commit only defect fixes discovered by a gate, using a focused fix commit after the relevant tests pass.

**Interfaces:**

- Consumes the four-tab app, offline CI contract, and manual live-eval command.
- Produces verification evidence, not new product behavior.

- [ ] **Step 1: Run every offline gate from the feature branch**

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m pytest -q
/Users/brianmar/workspace/censuschat/.venv/bin/pre-commit run --all-files
git status --short
```

Expected: all tests and hooks pass. Status contains only intentional changes or known pre-existing user changes.

- [ ] **Step 2: Start the local app and verify the API**

Run:

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/uvicorn src.app:app --port 8000
```

In a second terminal, verify:

```bash
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/evals
curl --fail "http://127.0.0.1:8000/api/traces?session_id=unknown"
```

Expected: health returns explicit current state, evals returns the committed benchmark, and unknown trace history returns an empty list rather than an error.

- [ ] **Step 3: Inspect the four-tab experience**

Verify in the browser:

- tab order is Chat, How It Works, Evidence, Evals;
- all four Example questions populate Chat without auto-submitting;
- How It Works explains the request lifecycle and three protection layers;
- Evidence loads one curated timeline and raw JSON is collapsed;
- Evals groups six regression and eight capability rows with tri-state counts;
- no old tab label, duplicate trace view, or standalone Data Source surface remains;
- mobile-width navigation remains usable.

- [ ] **Step 4: Ask for paid-call approval**

Stop and ask Brian immediately before executing the live command. Do not infer approval from earlier planning discussion.

- [ ] **Step 5: Run two live regression trials after approval**

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m evals.run_evals \
  --suite regression --ci --repeat 2 --output tmp/regression-ci.json
```

Expected: exit zero only if every regression scenario passes both trials and no regression check is inconclusive. CI mode leaves `evals/results/latest.json` unchanged.

- [ ] **Step 6: Inspect every trial rather than only the headline**

```bash
/Users/brianmar/workspace/censuschat/.venv/bin/python -m json.tool tmp/regression-ci.json
git diff -- evals/results/latest.json
```

Confirm:

- 12 scenario-trial results are present;
- every required check has an explicit outcome and observed evidence;
- model IDs, run times, and git SHA are recorded;
- `latest.json` has no diff;
- any fail or inconclusive result is preserved and explained rather than rerun until green.

- [ ] **Step 7: Hand off the verified build**

Report:

- offline test and hook totals;
- live regression outcomes by scenario and trial;
- actual live-run duration and available provider cost evidence;
- remaining capability failures or inconclusive checks;
- the exact claims the reviewer can safely make;
- deployment status, explicitly `not deployed` unless Brian separately requested deployment.

## Deadline Cut Order

If the build approaches eight hours, cut only in this order:

1. visual polish beyond a readable four-tab layout;
2. raw JSON styling beyond a functional collapsed disclosure;
3. model metadata display in the Evals tab while retaining it in the CI artifact;
4. capability grader changes other than known false-green fixes.

Do not cut result normalization, duplicate-tab deletion, protection-layer labels, the 6/8 suite partition, tri-state evidence, known false-green fixes, offline CI, or documentation truthfulness.

## Completion Criteria

- `run_census_sql` returns presentation-safe values for verified NULL and top-code cases.
- The app has exactly Chat, How It Works, Evidence, and Evals in that order.
- Four buttons are labeled Example questions in Chat.
- How It Works contains one request-flow diagram and clearly separates code protections, model instructions, and evaluation checks.
- Evidence uses one curated renderer and one trace store, with raw JSON collapsed.
- Turn Detail, Trace Logging, Data Source, and `docs/flow-diagram.html` are removed rather than hidden.
- `docs/solutions.html` is unchanged.
- All 14 executed scenarios belong to exactly one suite.
- The six canonical regression scenarios have strong deterministic checks.
- Incomplete grounding evidence is inconclusive, not a passing check.
- Regression inconclusive fails CI; capability inconclusive remains visible and non-blocking.
- Offline pytest is the required credential-free CI workflow.
- Paid regression evals are manual, protected, repeat twice, and cannot overwrite `latest.json`.
- README, How It Works, eval documentation, reflection, decisions, and code comments use the same accurate architecture claims.
- The complete offline suite and pre-commit hooks pass.
