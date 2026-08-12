# CLAUDE.md — censuschat invariants

These are rules, not guidance. Violating any of them is a defect. Deviating
requires an entry in `docs/decisions.md` and Brian's explicit approval first.
Requirement truth: `docs/assignment.pdf`. Decision truth:
`docs/01-architecture.md` (the original brief said `docs/plans/`; see
**D-006**). Interface truth: `src/contracts.py`.

Rules 15, 17, and 18 were knowingly deviated from during the build. The rule
text below is left unchanged on purpose — what was committed to and where it
was departed from are both evidence. Each deviation carries a `docs/decisions.md`
entry, flagged inline.

## Security & grounding

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

## Behavior

9. Bounded recovery: after a SQL error or zero-row result, at most 2 retries
   (re-search or rewrite), then honest failure explaining what was tried.
10. Genuine geography or intent ambiguity → ask the user. Resolvable
    defaults (e.g., ACS vintage) → assume and state the assumption in the
    answer.
11. Every user-facing turn streams `ChatEvent`s; every tool call emits
    `tool_start`/`tool_end`; a 50s watchdog is checked between tool-loop
    rounds and produces an honest partial answer before a later round; every
    stream terminates with `done` or `error` — no hangs, no blank responses,
    no unhandled exceptions reaching the client.

## Architecture

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
    *D-027 supersedes the historical five-tab implementation: the shipped
    reviewer surfaces are Chat, How It Works, Evidence, and Evals. The
    single-file/no-build half remains binding.*
16. Session state = full history replay from SQLite keyed by `session_id`.
17. Every turn is one Langfuse trace: `session_id` in metadata; spans for
    guardrail, each tool call, and each model call; token counts and
    latency recorded.
    *Not satisfied. Langfuse is not implemented. `src/tracing.py` persists
    local spans in SQLite, rendered solely in Evidence with cross-session
    history, but it has no Langfuse search or alerting. **D-021**, **D-023**.*
18. Deploy = Docker Compose (app + Caddy) on EC2 at
    `https://censuschat.brianmar.com` behind basic auth. Caddy reaches the
    app by compose service name, never `localhost`.
    *Caddy is native on the host (it also serves another site), so compose
    starts only `app`, published on `127.0.0.1:8000`. **D-016**.*

## Process

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
