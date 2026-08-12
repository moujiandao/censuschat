# CensusChat Interview Manual Design

Date: 2026-08-12

## Purpose

Create a private interview-preparation manual that helps the candidate explain
CensusChat clearly, defend the main engineering decisions, connect the build to
the assignment rubric, and state limitations without weakening the strongest
parts of the submission.

The manual is not a substitute README and is not intended for submission to the
reviewers. It is a speaking aid and technical reference for the follow-up
interview.

## Audience and tone

The primary reader is the candidate immediately before or during interview
preparation. Assume high familiarity with the product but uneven recall of file
locations, exact guarantees, and tradeoffs.

Use direct, technically mature language. Lead with the strongest defensible
claim, then name the relevant caveat once. Mark private coaching content
clearly, including likely questions, suggested answers, and claims to avoid.

## Source hierarchy

The manual must reconcile claims against these sources in order:

1. `docs/assignment.pdf` for the requirements and evaluation dimensions.
2. Current implementation under `src/`, `static/`, `evals/`, and `.github/`.
3. `README.md` for the reviewer path and operational instructions.
4. `docs/decisions.md` and `docs/reflection.md` for reasoning, deviations,
   limitations, and lessons learned.
5. Current verification output for test and health snapshots.

If prose conflicts with current code, current code controls. Pre-code design
documents may explain intent but cannot establish shipped behavior.

## Document structure

Target 12 to 15 US Letter portrait pages.

1. Cover and use note.
2. Ninety-second opening pitch.
3. Assignment rubric map.
4. Architecture at a glance.
5. One request, end to end.
6. Data model and statistical correctness.
7. Trust boundaries, guardrails, and failure handling.
8. Sessions, streaming, Evidence, and observability.
9. Testing and evaluation strategy.
10. Deployment and production-readiness assessment.
11. Example-question walkthrough.
12. Judgment under the 24-hour constraint.
13. Likely interview questions and concise answers.
14. One-page interview cheat sheet.

Sections may share pages when doing so improves scanability. Do not expand the
manual solely to hit the target page count.

## Core teaching narrative

The central architectural idea is a closed topology with open vocabulary. The
small, stable Census join and rollup rules are prompt context. Thousands of
variable labels and geography records are runtime data discovered through
local SQLite. This gives the agent broad coverage without putting a giant
schema catalog into the prompt.

The strongest production claim is the deterministic SQL trust boundary. Model
instructions and the classifier are soft layers. Every request-time Snowflake
query crosses an AST-based, default-deny validator before execution. The manual
must distinguish that hard guarantee from answer-grounding instructions and
offline evidence checks.

The judgment narrative is correctness over breadth. The build deliberately
restricts the data to the 2020 ACS five-year vintage, supports state and county
rollups from block-group data, prevents invalid median aggregation, and gives
honest redirects or refusals where the dataset cannot support the request.

## Required visuals

1. An architecture diagram showing browser, FastAPI, guardrail, agent loop,
   local discovery, SQL gate, Snowflake, streaming, and Evidence.
2. A request-sequence diagram showing state replay, classification, discovery,
   validation, execution, normalization, answer streaming, and trace storage.
3. A compact rubric-to-evidence matrix.
4. One current UI screenshot showing the four reviewer surfaces and New Chat
   control.
5. A testing pyramid or layered comparison showing deterministic tests, live
   regression evals, capability reporting, and human semantic review.

Every visual must teach a relationship that would be harder to retain from
prose alone. Avoid decorative screenshots or generic AI imagery.

## Interview coaching devices

Use three recurring callout types:

- `Say this`: concise reviewer-ready wording.
- `Why it matters`: the system-design principle behind a choice.
- `Do not overclaim`: a precise boundary on the evidence.

The likely-question section must cover classifier fail-open behavior, FTS
instead of embeddings, no agent framework, grounding evidence, median
aggregation, the 2020-only scope, horizontal scaling, the watchdog, tracing,
eval suite design, and prioritized next steps.

## Factual guardrails

The manual must state all of the following accurately:

- The app exposes exactly three agent tools.
- Variable and geography discovery are local SQLite operations.
- `run_census_sql` is the sole request-time Snowflake code path, but one user
  request may call it more than once.
- The classifier fails open on classifier failure. SQL validation fails closed.
- The 50-second watchdog is checked between rounds and cannot interrupt an
  in-flight model or database call.
- Runtime result normalization covers direct or simply aliased variables where
  lineage is known. It does not normalize derived or aggregate expressions.
- Sessions and traces persist in separate SQLite stores and remain
  single-instance infrastructure.
- Evidence is an application-local trace viewer, not a Langfuse replacement.
- The current serving path does not independently validate every number in the
  final answer.
- The committed benchmark artifact predates the current tri-state and suite
  contracts. It is legacy evidence and must not be presented as current proof.
- Capability outcomes are informational. Regression trials block only when the
  paid live workflow is deliberately run.
- No LLM judge is used because none has been calibrated against human labels.
- The public URL and health status must be verified at generation time rather
  than assumed from documentation.

## Visual design

Use a restrained white, navy, and Snowflake-blue palette with one amber accent
for caveats. Typography should resemble a polished engineering field guide:
large section numbers, compact body text, monospaced source paths, generous
margins, and strong page hierarchy.

Headers should identify the current section. Footers should show page number,
private-preparation status, and the repository commit used for the snapshot.
Use ASCII hyphens and avoid glyphs that do not render reliably.

## Output and generation

Final artifact:

`output/pdf/censuschat-interview-manual.pdf`

Maintain one generator source under `scripts/` so the document can be rebuilt
when the repository changes. Use ReportLab and bundled fonts or standard PDF
fonts. Temporary renders belong under `tmp/pdfs/` and are not committed.

## Verification

Before delivery:

1. Verify the deployed health endpoint or label it unavailable.
2. Record the repository commit and offline test result used by the manual.
3. Extract text from the generated PDF and scan for missing sections,
   placeholders, unsupported claims, and non-ASCII dash characters.
4. Render every PDF page to PNG.
5. Inspect every page for clipping, overlap, poor contrast, broken diagrams,
   awkward page breaks, and illegible code or table text.
6. Reopen the final PDF with `pypdf` and confirm page count and metadata.

## Definition of done

The candidate can use the manual to deliver a coherent five-minute overview,
walk through the four example questions, defend the major system-design
decisions, explain the eval strategy, and answer likely production questions
without claiming guarantees the code does not provide.
