# Deterministic Physical Table Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make variable search return the exact SQL-ready physical table so the model never derives `2020_CBG_B11012` from `B11012e1`.

**Architecture:** Extend the approved `VariableHit` contract with a required `physical_table` field. Derive it once in the local ACS search implementation, validate it against `ALLOWED_TABLES`, and instruct the model to copy the returned value exactly into SQL.

**Tech Stack:** Python 3.14, Pydantic, SQLite FTS5, pytest

**Spec:** `docs/superpowers/specs/2026-08-13-physical-table-routing-design.md`

## Global Constraints

- Preserve exactly three agent tools.
- Keep `validate_sql` as the default-deny trust boundary, with no SQL rewriting.
- Do not add dependencies or rebuild the SQLite snapshot schema.
- D-024 is the approved exception to the `src/contracts.py` interface freeze.
- Do not run `make eval` without separate approval because it makes paid Anthropic and Snowflake calls.
- Do not use em dashes in new prose.

---

### Task 1: Return an allowlisted physical table with every ACS variable hit

**Files:**
- Modify: `tests/test_tools.py`
- Modify: `src/contracts.py`
- Modify: `src/tools.py`

**Interfaces:**
- Consumes: `ALLOWED_TABLES`, `DEFAULT_VINTAGE`, and the existing ACS `variable_id` format.
- Produces: required `VariableHit.physical_table: str`, containing a fully qualified and quoted SQL table such as `US_CENSUS.PUBLIC."2020_CBG_B11"`.

- [ ] **Step 1: Write the failing routing tests**

Add these tests under the `search_census_variables` section of `tests/test_tools.py`:

```python
@pytest.mark.parametrize(
    ("variable_row", "query", "expected"),
    [
        (
            ("B11012e1", "B11012", "Households By Type", "Households", "total households text"),
            "total households",
            'US_CENSUS.PUBLIC."2020_CBG_B11"',
        ),
        (
            ("C15002e1", "C15002", "Tenure", "Occupied housing units", "tenure occupied text"),
            "tenure occupied",
            'US_CENSUS.PUBLIC."2020_CBG_C15"',
        ),
    ],
)
def test_search_returns_exact_allowlisted_physical_table(
    monkeypatch, variable_row, query, expected
):
    _seed_snapshot(monkeypatch, variable_rows=[variable_row])

    result = tools.search_census_variables(query)

    assert result.hits[0].physical_table == expected


def test_search_rejects_a_variable_without_an_allowlisted_physical_table(monkeypatch):
    variable_row = (
        "B06001e1",
        "B06001",
        "Place Of Birth",
        "Total population",
        "place birth text",
    )
    _seed_snapshot(monkeypatch, variable_rows=[variable_row])

    with pytest.raises(ValueError, match="no allowlisted physical table"):
        tools.search_census_variables("place birth")
```

The first test catches the reported full-logical-table bug, missing SQL quoting, and a B-only implementation. The second catches removal of allowlist validation.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PATH=/Users/brianmar/workspace/censuschat/.venv/bin:$PATH \
  pytest tests/test_tools.py::test_search_returns_exact_allowlisted_physical_table \
         tests/test_tools.py::test_search_rejects_a_variable_without_an_allowlisted_physical_table -q
```

Expected: the first test fails because `VariableHit` has no `physical_table`; the malformed-prefix test fails because no `ValueError` is raised.

- [ ] **Step 3: Add the required contract field**

Add this required field immediately after `variable_id` in `VariableHit`:

```python
physical_table: str
```

- [ ] **Step 4: Implement the minimal validated derivation**

Import `ALLOWED_TABLES` in `src/tools.py`, then add this helper beside the existing variable metadata helpers:

```python
def _physical_table_for_acs_variable(variable_id: str) -> str:
    unquoted = f"US_CENSUS.PUBLIC.{DEFAULT_VINTAGE}_CBG_{variable_id[:3]}"
    if unquoted not in ALLOWED_TABLES:
        raise ValueError(f"no allowlisted physical table for {variable_id}")
    prefix, table = unquoted.rsplit(".", 1)
    return f'{prefix}."{table}"'
```

Populate the new field in `search_census_variables`:

```python
VariableHit(
    variable_id=variable_id,
    physical_table=_physical_table_for_acs_variable(variable_id),
    # existing fields unchanged
)
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run the same focused pytest command from Step 2.

Expected: both tests pass.

- [ ] **Step 6: Run the complete tool test module**

Run:

```bash
PATH=/Users/brianmar/workspace/censuschat/.venv/bin:$PATH pytest tests/test_tools.py -q
```

Expected: all tool tests pass.

- [ ] **Step 7: Commit the contract and implementation**

```bash
git add src/contracts.py src/tools.py tests/test_tools.py
git commit -m "fix: return physical table with variable hits"
```

---

### Task 2: Make the agent consume the deterministic table field

**Files:**
- Modify: `src/agent.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `VariableHit.physical_table` serialized by the existing `_run_tool` path.
- Produces: model instructions that copy `physical_table` exactly instead of deriving a physical table from `TABLE_NUMBER`.

- [ ] **Step 1: Replace the prompt-level derivation**

In `src/agent.py`:

1. Update the `search_census_variables` system-prompt description to say each hit includes the exact SQL-ready `physical_table`.
2. Replace the `TABLE_NUMBER` prefix rule with:

```text
Every variable search hit includes physical_table, the exact SQL-ready table that contains the variable. Copy physical_table exactly into every FROM clause. Never derive or guess a table name from variable_id or TABLE_NUMBER.
```

3. Change the aggregation pattern to use `<physical_table>` directly:

```sql
SELECT SUM("<variable_id>") FROM <physical_table> WHERE SUBSTR(CENSUS_BLOCK_GROUP,1,5) = '<county_geo_id>'
```

4. Update the Anthropic tool description to mention `physical_table`.

Do not add a source-text assertion. Prompt prose is model behavior and belongs in the live eval, while the deterministic tool payload is covered by Task 1.

- [ ] **Step 2: Add the changelog entry**

Add this entry under a new `## [2026-08-13]` section at the top of `CHANGELOG.md`:

```markdown
### Fixed

- Return the exact SQL-ready `physical_table` with every ACS variable search hit (D-024), so the agent copies `2020_CBG_B11` for `B11012e1` instead of spending a recovery attempt on the nonexistent `2020_CBG_B11012`.
```

- [ ] **Step 3: Regenerate documentation references**

Run:

```bash
PATH=/Users/brianmar/workspace/censuschat/.venv/bin:$PATH make docs
```

Expected: the command succeeds and every generated ID-reference block recognizes D-024.

- [ ] **Step 4: Run formatting and full offline verification**

Run:

```bash
git diff --check
PATH=/Users/brianmar/workspace/censuschat/.venv/bin:$PATH make test
```

Expected: no whitespace errors and all 375 or more tests pass. The existing FastAPI deprecation warning is unrelated and may remain.

- [ ] **Step 5: Commit the agent and documentation changes**

```bash
git add src/agent.py CHANGELOG.md docs/decisions.md \
  docs/superpowers/plans/2026-08-13-physical-table-routing.md
git commit -m "fix: use deterministic physical table routing"
```

---

### Task 3: Review the completed code change

**Files:**
- Review: all files changed since `main`

**Interfaces:**
- Consumes: the completed implementation and test evidence.
- Produces: the required post-task code-review verdict.

- [ ] **Step 1: Invoke the code-reviewer subagent**

Give it the task description and this changed-file list:

```text
src/contracts.py
src/tools.py
src/agent.py
tests/test_tools.py
CHANGELOG.md
docs/decisions.md
docs/superpowers/specs/2026-08-13-physical-table-routing-design.md
docs/superpowers/plans/2026-08-13-physical-table-routing.md
```

- [ ] **Step 2: Resolve every BLOCKING finding**

If the verdict is FAIL, apply each blocking fix through a new RED/GREEN cycle where code behavior changes, rerun `make test`, and request another review.

- [ ] **Step 3: Record the handoff checkpoint**

Report the worktree path, branch, commits, test count, review verdict, and the fact that the paid live MT-01 eval was not run.
