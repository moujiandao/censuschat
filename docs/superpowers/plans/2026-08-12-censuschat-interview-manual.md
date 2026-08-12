# CensusChat Interview Manual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a polished, private PDF playbook that helps the candidate explain CensusChat, defend its system design, and map the implementation to `docs/assignment.pdf` without overstating the evidence.

**Architecture:** A single ReportLab generator owns the manual content, vector diagrams, page furniture, and PDF metadata. One current UI screenshot is captured as a source asset. Generation writes a stable final artifact under `output/pdf/`, then Poppler, `pypdf`, and `pdfplumber` provide structural and visual verification.

**Tech Stack:** Python 3, ReportLab, Pillow, pypdf, pdfplumber, Poppler, current CensusChat frontend.

## Global Constraints

- Treat `docs/assignment.pdf` as the requirements source of truth.
- Current code controls whenever prose and implementation disagree.
- The audience is private interview preparation, not reviewer-facing submission.
- Distinguish hard runtime guarantees from prompt instructions and eval evidence.
- Use US Letter portrait pages and ASCII hyphens only.
- Final output is `output/pdf/censuschat-interview-manual.pdf`.
- Temporary renders belong under `tmp/pdfs/` and are not committed.
- The application snapshot is commit `606e35b`, the latest app-code commit before manual documentation work.

---

### Task 1: Capture the verified app snapshot

**Files:**
- Create: `docs/assets/censuschat-reviewer-ui.png`
- Read: `docs/assignment.pdf`
- Read: `README.md`
- Read: `src/agent.py`
- Read: `src/sqlgate.py`
- Read: `src/tools.py`
- Read: `evals/scenarios.py`
- Read: `evals/results/latest.json`

**Interfaces:**
- Consumes: the running local UI and deployed `/api/health` endpoint.
- Produces: a current UI image and a verified factual snapshot for the generator.

- [ ] **Step 1: Verify the local and deployed app status**

Run:

```bash
curl -fsS http://127.0.0.1:8001/api/health
curl -fsS -u snowflake:census https://censuschat.brianmar.com/api/health
```

Expected: both return JSON containing `"status":"ok"`. If the deployed check fails, the manual must label the deployment unverified rather than infer health from the README.

- [ ] **Step 2: Capture the current reviewer UI**

Open `http://127.0.0.1:8001/` at desktop width, start a fresh chat, and capture the header, four tabs, New Chat control, and curated examples without personal browser chrome.

Expected: `docs/assets/censuschat-reviewer-ui.png` is a readable PNG whose visible labels match the current frontend.

- [ ] **Step 3: Confirm source facts**

Run:

```bash
git show --stat --oneline 606e35b
python -m pytest -q
python -c "import json; d=json.load(open('evals/results/latest.json')); print(d['run_at'], d['git_sha'], d['pass_rate'])"
```

Expected: application snapshot `606e35b`, 449 offline tests passing, and the committed benchmark identified as the legacy `d44c1cc` artifact from 2026-08-06.

- [ ] **Step 4: Commit the source snapshot**

```bash
git add docs/assets/censuschat-reviewer-ui.png
git commit -m "docs: capture reviewer interface"
```

### Task 2: Build the reproducible manual generator

**Files:**
- Create: `scripts/generate_interview_manual.py`
- Create: `output/pdf/censuschat-interview-manual.pdf`
- Read: `docs/superpowers/specs/2026-08-12-censuschat-interview-manual-design.md`

**Interfaces:**
- Consumes: `docs/assets/censuschat-reviewer-ui.png` and repository facts recorded in the approved design.
- Produces: `build_manual(output_path: Path) -> None` and the final PDF.

- [ ] **Step 1: Implement the document system**

Create ReportLab styles and flowables for section titles, compact body copy, callouts labeled `SAY THIS`, `WHY IT MATTERS`, and `DO NOT OVERCLAIM`, tables, page headers, footers, and vector diagrams.

The generator must set title metadata to `CensusChat Interview Manual`, author to `Brian Mar - private interview preparation`, and subject to `Snowflake Applied AI candidate homework interview playbook`.

- [ ] **Step 2: Implement the approved content structure**

Include the fourteen approved sections: opening pitch, rubric map, architecture, request lifecycle, data model, safety, sessions and Evidence, testing and evals, production, example walkthrough, constraint judgment, likely questions, and one-page cheat sheet.

Required factual statements include:

```text
Three agent tools, two local discovery tools, one Snowflake execution path.
Haiku classifier fails open; AST SQL gate fails closed.
The 50-second watchdog is a between-round soft deadline.
Serving-time numeric grounding is instructed, not independently enforced.
The committed 14/14 benchmark is legacy evidence, not current regression proof.
No LLM judge is used without calibration against human labels.
```

- [ ] **Step 3: Generate the PDF**

Run:

```bash
python scripts/generate_interview_manual.py
```

Expected: `output/pdf/censuschat-interview-manual.pdf` exists, is non-empty, and contains 12 to 16 pages.

- [ ] **Step 4: Run structural validation**

Run:

```bash
pdfinfo output/pdf/censuschat-interview-manual.pdf
pdftotext output/pdf/censuschat-interview-manual.pdf - | rg "90-SECOND OPENING|ASSIGNMENT RUBRIC|DO NOT OVERCLAIM|INTERVIEW CHEAT SHEET"
```

Expected: Letter page size, expected metadata, and all required sections present.

### Task 3: Render, inspect, and finalize

**Files:**
- Modify: `scripts/generate_interview_manual.py` only if visual defects require correction.
- Regenerate: `output/pdf/censuschat-interview-manual.pdf`
- Create temporarily: `tmp/pdfs/censuschat-manual/page-*.png`

**Interfaces:**
- Consumes: PDF from Task 2.
- Produces: visually verified final PDF.

- [ ] **Step 1: Render every page**

Run:

```bash
mkdir -p tmp/pdfs/censuschat-manual
pdftoppm -png -r 120 output/pdf/censuschat-interview-manual.pdf tmp/pdfs/censuschat-manual/page
```

Expected: one PNG per PDF page.

- [ ] **Step 2: Inspect all rendered pages**

Check every page for clipping, overlap, broken tables, weak contrast, unreadable diagrams, awkward page breaks, incorrect headers or footers, and malformed glyphs.

- [ ] **Step 3: Correct and regenerate**

For every discovered defect, edit the generator, regenerate the PDF, rerender all pages, and inspect the affected pages plus adjacent page transitions.

- [ ] **Step 4: Run final verification**

Run:

```bash
python -m pytest -q
python - <<'PY'
from pathlib import Path
from pypdf import PdfReader

path = Path("output/pdf/censuschat-interview-manual.pdf")
reader = PdfReader(path)
assert 12 <= len(reader.pages) <= 16
assert reader.metadata.title == "CensusChat Interview Manual"
assert path.stat().st_size > 100_000
print(len(reader.pages), path.stat().st_size)
PY
git diff --check
```

Expected: full offline suite passes, PDF metadata and size checks pass, and the diff is clean.

- [ ] **Step 5: Request code review and commit**

Request a read-only code review of `scripts/generate_interview_manual.py`, `docs/assets/censuschat-reviewer-ui.png`, and `output/pdf/censuschat-interview-manual.pdf`. Resolve every blocking issue, then commit:

```bash
git add scripts/generate_interview_manual.py docs/assets/censuschat-reviewer-ui.png output/pdf/censuschat-interview-manual.pdf
git commit -m "docs: add CensusChat interview manual"
```
