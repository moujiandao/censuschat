# AGENTS.md: censuschat invariants

**This file is the source of truth for every agent working in this repo**, and
for humans following the same rules. `CLAUDE.md` is a symlink to this file, so
Claude Code auto-loads it, Codex and other agents find it under the name they
look for, and there is no second copy that can drift. A comment anywhere in the
codebase reading "CLAUDE.md rule 14" means rule 14 below; the numbering has one
home.

The rules in [Invariants](#invariants) are rules, not guidance. Violating any of
them is a defect. Deviating requires an entry in `docs/decisions.md` and Brian's
explicit approval first. Everything after the invariants is operational: how to
get oriented, what to run, and what not to do.

| Truth | Lives in |
|---|---|
| Requirements | `docs/assignment.pdf` |
| Decisions | `docs/01-architecture.md` (the original brief said `docs/plans/`; see **D-006**) |
| Interfaces | `src/contracts.py` |
| Deviations | `docs/decisions.md`, as `D-0NN` entries |

Rules 15, 17, and 18 were knowingly deviated from during the build. The rule
text below is left unchanged on purpose — what was committed to and where it
was departed from are both evidence. Each deviation carries a `docs/decisions.md`
entry, flagged inline.

---

## Invariants

### Security & grounding

1. User text is NEVER interpolated into SQL. All SQL reaches Snowflake only
   through `run_census_sql`, which must pass `validate_sql`: sqlglot parse
   (dialect="snowflake"), single statement, SELECT-only, table allowlist,
   LIMIT injected, `STATEMENT_TIMEOUT_IN_SECONDS` set on the session.
2. Every numeric claim in an assistant answer must come from rows returned by
   this turn's QueryResults. Zero rows is an honest "not found," never a
   number.
3. Census variables are data, not prompt content. Never enumerate variable
   IDs or labels in any prompt; discovery happens only via
   `search_census_variables` at runtime.
4. The agent has exactly three tools: `search_census_variables`,
   `resolve_geography`, `run_census_sql`. No new tools without approval.
5. Guardrail enforcement lives in code. The classifier and prompt
   instructions are soft layers; the SQL gate is the trust boundary.
6. The guardrail classifier receives recent conversation turns and fails
   OPEN (allow) on its own errors or timeouts.
7. Sentinel/suppressed values (codes per `docs/schema-notes.md`) are never
   rendered to the user as real numbers.
8. Secrets live only in `.env` / deployment env. The Snowflake private key
   never enters the repo. `.env.example` documents every variable with no
   values.

### Behavior

9. Bounded recovery: after a SQL error or zero-row result, at most 2 retries
   (re-search or rewrite), then honest failure explaining what was tried.
10. Genuine geography or intent ambiguity → ask the user. Resolvable
    defaults (e.g., ACS vintage) → assume and state the assumption in the
    answer.
11. Every user-facing turn streams `ChatEvent`s; every tool call emits
    `tool_start`/`tool_end`; a 50s watchdog ends tool use with an honest
    partial answer; every stream terminates with `done` or `error` — no
    hangs, no blank responses, no unhandled exceptions reaching the client.

### Architecture

12. `src/contracts.py` is the interface freeze. Signatures, field names, and
    enum members change only with a flagged, approved deviation. Items
    marked PROVISIONAL resolve only from `docs/schema-notes.md` evidence.
13. Variable search and geography resolution run against local SQLite
    snapshots only. At request time, Snowflake is touched solely by
    `run_census_sql`.
14. No agent frameworks (LangChain, LangGraph, etc.). Anthropic SDK +
    FastAPI + sqlglot + snowflake-connector-python. Models pinned in one
    config module: Sonnet for the agent, Haiku for the classifier.
15. Frontend is one static HTML file (vanilla JS, CDN assets only, no build
    step) with three tabs: Chat, Evals, Flow Diagram.
    *Shipped with five tabs — Trace Logging and Data Source added, and "Flow
    Diagram" ships as **Turn Detail** (it shows one turn's real events, not a
    diagram). The tab list is a minimum surface, not a cap; the
    single-file/no-build half is the binding half and holds. **D-017**.*
16. Session state = full history replay from SQLite keyed by `session_id`.
17. Every turn is one Langfuse trace: `session_id` in metadata; spans for
    guardrail, each tool call, and each model call; token counts and
    latency recorded.
    *Not satisfied. Langfuse was cut for time; the span model shipped as
    in-process tracing (`src/tracing.py`, Trace Logging tab) with no
    persistence, no cross-session search, no alerting. **D-021**.*
18. Deploy = Docker Compose (app + Caddy) on EC2 at
    `https://censuschat.brianmar.com` behind basic auth. Caddy reaches the
    app by compose service name, never `localhost`.
    *Caddy is native on the host (it also serves another site), so compose
    starts only `app`, published on `127.0.0.1:8000`. **D-016**.*

### Process

19. TDD (failing test first) on deterministic layers: `validate_sql`,
    `resolve_geography` ranking, guardrail routing logic, FTS retrieval
    scoring, `normalize_value`. LLM behavior is tested by golden evals, not
    mocked unit asserts.
20. `make eval` runs the golden set and writes
    `evals/results/<timestamp>.json` plus `latest.json` (EvalRun schema).
    Result JSONs are committed. Red rows are kept and triaged — never
    deleted to look clean.
21. Small commits mapped to GitHub issues. Tracer bullet ships before any
    parallel work; git worktrees only for lanes touching disjoint files
    (see architecture §Parallelization).
22. When the hour budget runs out: cut features, never the reflection.

---

## Getting oriented

Read in this order. The invariants above come first; these fill in why they are
shaped the way they are.

| Order | File | What it is |
|---|---|---|
| 1 | `docs/decisions.md` | Every deviation and non-obvious decision, as a `D-0NN` entry with reasoning |
| 2 | `src/contracts.py` | Frozen interfaces (rule 12) |
| 3 | `docs/01-architecture.md` | A pre-code design document, so §5, §6 and §7 are superseded by what shipped |
| 4 | `README.md` | Reviewer-facing tour: running demo, request lifecycle, trust boundary, evals |

## Repo map

```
src/
  app.py            FastAPI app, SSE streaming, /api/* endpoints
  agent.py          the tool loop (Sonnet), bounded recovery, watchdog
  guardrail.py      pre-turn classifier (Haiku), fails OPEN on its own errors
  sqlgate.py        validate_sql: the trust boundary
  tools.py          the three tools
  contracts.py      frozen interfaces
  model_config.py   the only place model IDs are pinned
  snapshot.py       local SQLite snapshot of variables + geographies
  sessions.py       history replay keyed by session_id
  tracing.py        in-process spans, persisted to SQLite (D-023)
  snowflake_conn.py  health.py  us_states.py
static/index.html   the entire frontend: one file, vanilla JS, CDN only, no build step
evals/              golden set (scenarios.py), runner, committed results/
tests/              pytest suite, mirrors src/ module by module
scripts/            check_env.py, build_id_reference.py, sf_query.py
docs/               invariant deviations, architecture, schema notes, reflection
data/               gitignored SQLite: snapshot, sessions, traces
```

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in credentials
python scripts/check_env.py   # verifies every credential actually works

make test                     # pytest, the full suite
make eval                     # golden set against the REAL stack (see below)
make docs                     # regenerate the id-reference tables in docs
uvicorn src.app:app --reload  # http://localhost:8000/
```

`make deploy` and `make deploy-status` talk to the production EC2 host over
SSH. **Do not run them unless Brian explicitly asks.** `make deploy` refuses a
dirty tree and refuses to run off `main`, by design.

## What needs credentials and network

This matters if you are running in a sandbox without network access, which is
the common case for Codex.

| Task | Needs |
|---|---|
| `make test` | Nothing. The suite is offline and hermetic. This is your feedback loop. |
| First app boot | Snowflake, to build `data/snapshot.sqlite3`. Later boots reuse the file. |
| `make eval` | Real Anthropic **and** real Snowflake. It is a live-call harness, costs money, and takes about 2.6 minutes. Do not run it speculatively. |
| `make deploy` | SSH to the production host. Ask first. |

If you cannot reach the network, say so and work against `make test`. Do not
mock out Snowflake or Anthropic to fake an eval result: a fabricated green run
is worse than no run, and rule 20 exists because of exactly this temptation.

## Working conventions

- **TDD on deterministic layers** (rule 19). Failing test first for
  `validate_sql`, `resolve_geography` ranking, guardrail routing, FTS scoring,
  `normalize_value`. Behavior of the model itself belongs in `evals/`.
- **Run the full suite before committing.** Pre-commit hooks enforce it. Never
  use `--no-verify`.
- **Small commits** (rule 21), one logical change each, branched off `main`
  with a `feat/`, `fix/` or `chore/` prefix.
- **No em dashes in prose you write.** Commas, periods, parentheses or regular
  dashes instead. The invariant text above predates this and is quoted
  verbatim on purpose.
- **Do not add dependencies casually.** `requirements.txt` is pinned and short
  on purpose (rule 14).

## When your change is architectural

Before closing the task, for any new or renamed module, changed data flow,
added or removed tool, or dependency change:

1. `CHANGELOG.md`: `## [YYYY-MM-DD]`, then `### Added` / `### Changed` /
   `### Removed`. Imperative mood, one entry per change.
2. `docs/decisions.md`: a new `D-0NN` entry if you deviated from an invariant,
   or made a decision a future reader would otherwise have to reverse-engineer.
3. This file, if the architecture it describes moved, or if the repo map or
   commands changed. Use targeted edits. Never rewrite it in one operation,
   and back it up first: it is the rulebook, and `CLAUDE.md` points at it.
4. `make docs` if you referenced a `D-0NN` or scenario id in a doc.
   `tests/test_id_reference.py` fails if a doc is stale, so this is enforced.

## Do not

- Build SQL by string-formatting anything a user typed. Ever (rule 1).
- Print a number that did not come from this turn's query results (rule 2).
- Enumerate census variable IDs or labels inside a prompt (rule 3).
- Add a fourth agent tool (rule 4), or a second place where a model ID is
  named (rule 14).
- Delete or edit a red eval row to make a run look green (rule 20).
- Add a build step, a bundler, or an npm dependency to `static/index.html`.
  Single file, no build, CDN assets only. That half of rule 15 is binding.
- Replace the `CLAUDE.md` symlink with a real file. Two copies of the
  invariants is the exact failure this layout prevents.
- Run `docker compose config` on a populated host. It inlines `env_file` and
  prints every secret in `.env` as plaintext.
- Commit `.env`, a `.pem`, or anything under `data/`.
- Reinterpret an invariant to make it match what you built. Record the
  deviation instead.
