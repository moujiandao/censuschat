# CensusChat Reviewer-Clarity Design

**Date:** 2026-08-11
**Status:** Approved in conversation, pending written-spec review
**Timebox:** One working day, approximately eight hours
**Motivation:** `docs/assignment.pdf`

## Objective

Make CensusChat easy to understand, explain, and assess without expanding it into a broader production platform. The app should communicate one coherent system story:

> A user asks a Census question. The agent discovers the relevant variable and geography, passes SQL through a deterministic safety gate, queries Snowflake through one controlled path, normalizes returned values, and exposes evidence showing what happened.

The implementation should demonstrate sound system-design judgment through a few clear seams and honest claims. It should not add architecture merely to appear production-grade.

## Success Criteria

A reviewer should be able to answer these questions after using the app for five minutes:

1. What problem does CensusChat solve?
2. How does a question become a Census answer?
3. Which protections are enforced by code?
4. Which behaviors still depend on the model?
5. What evidence supports one answer?
6. What do the automated evaluations prove and not prove?

The build succeeds when:

- the app has four top-level tabs with distinct purposes;
- duplicated technical surfaces are removed rather than hidden;
- four example questions demonstrate the primary behaviors;
- the request lifecycle is explained once with consistent terminology;
- normalization is part of the query-result contract;
- six objectively scored scenarios form the regression suite;
- eight broader scenarios remain visible as capability evidence;
- eval outcomes distinguish pass, fail, and inconclusive;
- offline tests are the required CI gate;
- paid live evals remain manually triggered;
- current limitations are stated accurately.

## Non-Goals

This timebox does not include:

- complete runtime validation of every final answer number;
- buffering and repairing candidate answers;
- a complete query-evidence ledger;
- semantic variable and geography lineage inside the SQL gate;
- session-admission redesign;
- an absolute cross-provider cancellation deadline;
- readiness or deployment architecture changes;
- nightly capability runs;
- required paid evals on pull requests;
- an LLM judge;
- new Census vintages or data sources;
- a new frontend build system or dependency.

These may remain roadmap items. They must not be described as shipped guarantees.

## Product Information Architecture

The final top-level navigation is:

```text
Chat  →  How It Works  →  Evidence  →  Evals
```

### Chat

Chat remains the primary product. It contains four clearly labeled **Example questions**:

1. A direct factual question with a stable exact answer.
2. A follow-up question that reuses conversational context.
3. An ambiguous geography question that requires clarification.
4. A request for unsupported future data that requires an honest refusal.

These examples are usage guidance, not a separate demo mode or test suite. Off-topic and prompt-injection behavior remain automated regression cases but do not occupy the primary product explanation.

### How It Works

How It Works replaces the conceptual roles previously spread across Data Source, standalone flow diagrams, and rule descriptions. It contains:

1. one primary request-flow diagram;
2. a concise description of the three agent tools;
3. the local SQLite versus Snowflake data-access split;
4. value-cleanup rules;
5. a three-part protection model;
6. a compact explanation of evaluation;
7. current known limits.

The three-part protection model uses these exact labels:

- **Code protections:** deterministic checks that reject or transform behavior at runtime.
- **Model instructions:** behavior requested through the system prompt but not independently guaranteed by the serving path.
- **Evaluation checks:** behavior measured on selected scenarios outside the serving path.

This distinction prevents a prompt instruction or passing eval from being misrepresented as a universal runtime guarantee.

### Evidence

Evidence replaces Turn Detail and Trace Logging. It presents one stored trace through a curated timeline:

1. guardrail decision;
2. variable discovery;
3. geography resolution;
4. SQL validation;
5. query result summary;
6. final answer;
7. timing and terminal status.

The default view is explanatory rather than a generic JSON dump. Raw trace JSON remains available behind an optional disclosure for technical inspection. Evidence reads from the existing trace store and does not create a second observability pipeline.

### Evals

Evals remains a reviewer-facing summary, not a CI dashboard. It shows:

- the latest committed benchmark;
- regression and capability groupings;
- pass, fail, and inconclusive counts;
- model identifiers and run timestamp when available;
- per-scenario details already present in the artifact;
- a concise note that operational CI artifacts live in GitHub Actions.

The app does not fetch GitHub Actions history, expose workflow controls, or mirror CI logs.

## Request Flow and System Seams

The primary diagram communicates this flow:

```text
User question
  → guardrail classification
  → agent chooses among three tools
  → local variable and geography discovery
  → deterministic SQL validation
  → Snowflake execution through run_census_sql
  → value normalization
  → model answer
  → stored bounded trace
```

The design emphasizes four existing seams rather than introducing new public abstractions:

### Agent orchestration seam

`src/agent.py` owns conversation replay, tool choice, bounded recovery, progress events, and final response generation. The model reasons, but it does not directly access Snowflake.

### Local discovery seam

`search_census_variables` and `resolve_geography` use local SQLite snapshots. Census variables remain runtime data rather than prompt content.

### Query safety seam

`run_census_sql` is the sole request-time Snowflake path. `validate_sql` hides parsing, statement restrictions, table allowlisting, limit injection, and timeout configuration behind one interface.

### Evaluation seam

The eval runner sends real scenarios through the same application behavior, observes bounded events, applies deterministic checks, and writes a result artifact. Evaluation does not change serving-path behavior.

## Runtime Claim Boundary

The deadline build makes these claims:

- SQL safety is code-enforced.
- Snowflake has one request-time access path.
- variable and geography discovery occur through the three declared tools;
- returned values are normalized before the model sees them;
- retries and tool-loop behavior are bounded by existing code;
- selected behaviors are checked by deterministic evals.

The deadline build does not claim that every final answer number is independently validated at runtime. The accurate grounding statement is:

> Answer grounding is instructed in the system prompt and checked on selected eval scenarios. SQL safety is the deterministic runtime trust boundary.

The watchdog is described according to its actual behavior. It is not called an absolute 50-second guarantee unless in-flight model and tool calls can be interrupted by the implementation.

## Value Normalization

Normalization becomes a private part of the `run_census_sql` result contract. It is not a fourth agent tool or a separate public pipeline stage.

The Snowflake result path becomes:

```text
raw row → normalize each data cell → presentation-safe QueryResult → model
```

Only rules verified against the current dataset are implemented:

| Input | Model-facing value | Reason |
|---|---|---|
| SQL `NULL` in a data cell | `not reported` | Absence or suppression is not zero. |
| `B19013e1 = 250001` | `$250,000 or more` | The value is a documented top-code, not a literal $250,001 median. |
| Ordinary numeric value | Original numeric value | Formatting must not change meaning. |
| Geography or identifier value | Original value | Identifiers are not demographic measurements. |

`docs/schema-notes.md` is the technical source of truth for the observed Snowflake behavior and affected variables. How It Works presents the plain-language summary. Tests are the executable enforcement.

The current share uses SQL `NULL` rather than numeric Census sentinel codes. The UI must not imply that unobserved sentinel transformations are currently active.

## Evaluation Design

### Why 14 scenarios remain

The current 14 scenarios are historical, not a statistically selected target. Twelve came from the pre-code PRD, and two were added from observed failures. This build strengthens their scoring before expanding coverage.

New scenarios are added only when they:

1. represent an observed or clearly required failure mode;
2. have a stable oracle;
3. test behavior not already covered;
4. have actually been executed.

### Regression suite

The blocking regression suite is exactly:

- `DF-05`: stable direct fact;
- `MT-01`: multi-turn context retention;
- `AMB-01`: ambiguity handling;
- `UN-01`: unsupported request refusal;
- `OT-01`: off-topic handling;
- `INJ-02`: prompt-injection resistance.

Regression scenarios are important and objectively measurable. Every required check must pass. An inconclusive regression check is blocking because a merge gate must be decisive.

### Capability suite

The capability suite contains the remaining eight currently executed scenarios:

- `DF-01`;
- `CMP-01`;
- `AMB-02`;
- `PM-02`;
- `PM-03`;
- `AMB-03`;
- `UN-08`;
- `PM-08`.

Capability scenarios report broader, stochastic, statistically nuanced, or incompletely instrumented behavior. They do not block merges in this build.

### Outcome semantics

- **Pass:** available evidence proves the declared assertion.
- **Fail:** available evidence contradicts the declared assertion.
- **Inconclusive:** captured evidence cannot prove or disprove the assertion.

Inconclusive is not removed from the denominator. The UI reports pass, fail, and inconclusive counts over all scenarios. Every inconclusive result includes a concrete reason. A capability scenario cannot become regression until it produces stable binary outcomes.

### Grader priorities

The existing scenario count is preserved while weak checks are strengthened. In particular:

- answerable scenarios require a nonempty answer;
- stable factual scenarios require expected variable, geography, and exact value evidence;
- comparison scenarios must verify the comparison outcome, not only both geographies;
- multi-turn scenarios must verify the final variable and answer, not only an earlier geography;
- refusal scenarios require a completed refusal with no agent tool or SQL execution;
- ambiguity scenarios require a clarifying question and no SQL execution;
- median variables may not be incorrectly summed or averaged across geographies;
- incomplete multi-row grounding evidence returns inconclusive rather than pass.

No LLM judge is added. Subjective prose and statistical explanation remain human-reviewed until a labeled set exists and a candidate judge is calibrated against it.

## CI Design

### Required offline workflow

The required GitHub Actions check:

- runs the complete offline pytest suite;
- triggers on pull requests and pushes to `main`;
- uses pinned repository dependencies;
- has no Anthropic, Snowflake, or deployment credentials;
- uses a strict timeout and concurrency cancellation;
- never uses `pull_request_target`.

### Manual live-eval workflow

Paid live evals remain manually triggered through `workflow_dispatch` during this build. The workflow:

- uses a protected `live-evals` environment;
- runs two regression trials;
- writes to an explicit artifact path;
- never overwrites `evals/results/latest.json`;
- uploads the JSON artifact even when the run fails;
- records enough provenance to identify models, time, and git revision;
- does not run untrusted fork code with secrets.

Nightly capability runs and required paid pull-request checks are deferred.

## Cleanup and Preservation

Cleanup is deletion, not CSS hiding or unreachable legacy markup.

Remove from `static/index.html`:

- the Turn Detail top-level tab and its duplicate rendering path;
- the Trace Logging top-level tab and its duplicate rendering path;
- the Data Source top-level tab and its standalone explanatory content;
- references that instruct users to visit removed tabs.

Replace those surfaces with How It Works and Evidence using the existing data sources.

Move the useful request-flow content from `docs/flow-diagram.html` into How It Works, then remove the standalone file so there is one current reviewer-facing diagram.

Preserve `docs/solutions.html`. It is a long-form assignment explanation and potential PDF source, not a competing application surface.

Do not add a standalone eval-flow HTML page. Explain evaluation compactly within How It Works and Evals.

## Error Handling and Honesty

- A failed API call produces a visible error state rather than an empty panel.
- Evidence distinguishes missing trace data from a successful turn with no tool calls.
- Evals distinguishes a missing committed artifact from a run with zero scenarios.
- How It Works labels current limits rather than implying deferred protections exist.
- Normalization handles only documented data behavior and leaves unknown values unchanged.
- Existing bounded recovery and terminal SSE behavior remain intact.

## Testing Strategy

Use TDD for every deterministic behavior changed in this build.

### Required focused coverage

- normalization through the real `run_census_sql` return path;
- ordinary values, SQL NULL, the `B19013e1` top-code, and identifier preservation;
- exact four-tab order and removal of legacy tab labels;
- curated Evidence rendering with optional raw disclosure;
- example-question labels and content;
- regression/capability partition with no overlap and complete coverage of the 14 scenarios;
- pass, fail, and inconclusive semantics;
- regression failure on inconclusive;
- stronger scenario assertions;
- CI workflow safety properties.

The complete offline pytest suite runs before every commit. Paid eval execution requires separate approval immediately before the call.

## Execution Cut Line

If the timebox is at risk, cut in this order:

1. cosmetic styling beyond readable layout;
2. optional raw-trace presentation improvements;
3. live-workflow provenance fields beyond model, timestamp, and git revision;
4. capability grader improvements that do not correct a known false green.

Do not cut:

- removal of duplicate top-level UI surfaces;
- accurate protection labels;
- normalization wiring and tests;
- regression/capability partition;
- correction of known false-green graders;
- offline CI;
- documentation truthfulness.

## Acceptance Criteria

- Navigation is exactly Chat, How It Works, Evidence, Evals.
- Chat labels four curated prompts as Example questions.
- How It Works contains one primary request-flow diagram.
- How It Works distinguishes Code protections, Model instructions, and Evaluation checks.
- Evidence presents one curated turn timeline and hides raw JSON by default.
- Turn Detail, Trace Logging, and Data Source no longer exist as top-level tabs or hidden implementations.
- `docs/flow-diagram.html` is removed after its useful content moves into the app.
- `docs/solutions.html` remains intact.
- `run_census_sql` returns model-facing `not reported` for demographic SQL NULL and `$250,000 or more` for the documented `B19013e1` top-code.
- `docs/schema-notes.md`, How It Works, and normalization tests agree.
- All 14 executed scenarios belong to exactly one suite.
- Regression contains the six approved IDs.
- Eval summaries include pass, fail, and inconclusive without excluding inconclusive from totals.
- Offline pytest is the only required automated pull-request gate.
- Live evals are manual, protected, and artifact-producing.
- Reviewer-facing claims match implemented behavior.
- The complete offline suite passes.
