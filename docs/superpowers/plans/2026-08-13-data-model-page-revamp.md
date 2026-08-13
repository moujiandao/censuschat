# Data Model Page Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dense data-model manual page with an intuitive, example-led explanation of Census grain and valid aggregation.

**Architecture:** Keep the existing ReportLab generator and `build_manual()` interface. Change only the section composition, its PDF contract assertions, and the regenerated artifact.

**Tech Stack:** Python 3, ReportLab, pypdf, Poppler, pytest.

## Global Constraints

- Preserve the existing 15-page Letter document and visual system.
- Use ASCII hyphens only.
- Keep source claims aligned with `docs/schema-notes.md`, `docs/decisions.md`, and `src/tools.py`.
- Do not change the application runtime or production dependencies.

---

### Task 1: Simplify the data-model page

**Files:**
- Modify: `tests/test_interview_manual.py`
- Modify: `scripts/generate_interview_manual.py:808-850`
- Regenerate: `output/pdf/censuschat-interview-manual.pdf`

**Interfaces:**
- Consumes: `build_manual(output_path: Path) -> None` and the existing manual styles.
- Produces: a one-page section organized as grain, valid rollup, invalid rollup, quick rules, and vintage decision.

- [ ] **Step 1: Write the failing PDF contract assertions**

Require generated text to include:

```python
assert "1. Start with the smallest unit" in text
assert "Harris County population = sum of its block-group population counts" in text
assert "Neighborhood median incomes: $55k and $95k" in text
assert "does not make the county median $75k" in text
assert "QUICK RULES" in text
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m pytest -q tests/test_interview_manual.py::test_build_manual_contract
```

Expected: failure because the simplified examples are absent.

- [ ] **Step 3: Replace the dense content**

Update `add_data_model()` to render three short cards:

```text
1. Start with the smallest unit
2. Add counts to answer larger-geography questions
3. Do not combine medians
```

Add a quick-rules strip for matching universes, missing values, supported geography, and the 2020-only vintage. Retain a short explanation of why 2015-2019 was excluded.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
python -m pytest -q tests/test_interview_manual.py
```

Expected: both PDF contract tests pass.

- [ ] **Step 5: Regenerate and inspect**

Run:

```bash
make manual
pdftocairo -f 6 -l 6 -png -r 140 output/pdf/censuschat-interview-manual.pdf tmp/pdfs/data-model/page
```

Inspect page 6 for hierarchy, legibility, clipping, overlap, and intuitive reading order.

- [ ] **Step 6: Verify and commit**

Run the complete offline pytest suite, `git diff --check`, and a PDF metadata/page-count check. Request the required code review, resolve every blocking issue, then commit the generator, test, regenerated PDF, design, and plan.
