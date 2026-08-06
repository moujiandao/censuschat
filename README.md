# censuschat

A chat agent that answers natural-language questions about US demographics,
grounded in the 2020 ACS 5-year estimates (SafeGraph's Open Census Data
share on the Snowflake Marketplace).

**Live demo:** https://censuschat.brianmar.com
**Credentials:** HTTP Basic Auth — username `TODO`, password `TODO` *(fill
in before sharing — see `.env` on the deploy host for `BASIC_AUTH_USER` /
the plaintext password used to generate `BASIC_AUTH_HASH`)*.

> **Note on the live demo's freshness:** this session's redeploy step could
> not run from inside the sandboxed dev environment (no outbound SSH). If
> you're reading this and the demo doesn't yet reflect the frontend or the
> SQL-identifier-quoting fix described below, `./deploy.sh` needs to be run
> on the EC2 host (`git pull --ff-only && docker compose up -d --build`) —
> see "Deploying" below.

## Architecture

```
Browser (static/index.html, vanilla JS)
   │  SSE over POST /api/chat
   ▼
FastAPI (src/app.py)
   │
   ▼
agent_turn (src/agent.py) — hand-written Sonnet tool loop, no agent framework
   ├─ guardrail (src/guardrail.py, Haiku)         — fails OPEN on error/timeout
   ├─ degraded-mode check (src/health.py)         — checked once at boot, cached
   ├─ search_census_variables / resolve_geography — local SQLite (FTS5), never touches Snowflake
   └─ run_census_sql                              — the ONLY path to Snowflake, gated by validate_sql
        └─ src/sqlgate.py — sqlglot parse, SELECT-only, table allowlist, LIMIT injection, STATEMENT_TIMEOUT
```

Session state is full-history replay from SQLite, keyed by `session_id`
(`src/sessions.py`) — no summarization, no vector store. The local
snapshot (`src/snapshot.py`) is built once at startup from Snowflake's
variable-metadata and geography tables, so per-request variable search and
geography lookups are pure local SQLite — Snowflake is touched at request
time solely by `run_census_sql`.

Full interface contract lives in `src/contracts.py` (docstring-first,
treated as frozen — see its header). Design decisions and interpretation
calls on ambiguous requirements are logged in `docs/decisions.md` (D-001
through D-015); the original PRD is `docs/plans/02-prd.md`.

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in Snowflake + Anthropic credentials
uvicorn src.app:app --reload
```

Then open `http://localhost:8000/`. `/api/health` reports snapshot and
Snowflake status. First boot builds the local variable/geography snapshot
from Snowflake (a few seconds); subsequent boots reuse the cached file at
`data/snapshot.sqlite3`.

## Deploying

`docker-compose.yml` + `Caddyfile` run the app behind Caddy (TLS + basic
auth) on an EC2 host. From that host, with `.env` populated per
`.env.example`'s comments:

```bash
./deploy.sh   # git pull --ff-only && docker compose up -d --build, waits for /api/health
```

## Interpreting the requirements

The assignment intentionally leaves several decisions open-ended. Where I
made a judgment call, it's logged in `docs/decisions.md`; the notable ones:

- **Dataset**: SafeGraph's Open Census Data share (D-001), not the
  Cybersyn share the original brief assumed — verified by checking
  `SHOW DATABASES`'s actual origin string.
- **"Conflicting" questions** (a required edge case): interpreted as the
  median-aggregation trap — you cannot SUM or AVG a median across block
  groups. When a true mean is computable (a numerator/denominator pair
  exists), the agent substitutes `SUM(numerator)/SUM(denominator)` and
  states the substitution explicitly rather than silently guessing or
  refusing (D-002).
- **"Partially match available data"**: city/place questions. This
  dataset has no city/place boundaries, only state and county — the agent
  states that plainly and offers the containing county as an explicit
  substitute the user must confirm, never silently equating a city with
  its (larger) county (D-005).
- **Ambiguous geography**: county-name collisions are real and measurable
  here (30 states have a "Washington County") — the agent lists every
  candidate and asks, both as a system-prompt instruction and as a
  code-enforced backstop that blocks `run_census_sql` outright if the
  model tries to proceed on an unresolved ambiguity (D-014).

## Testing

287 tests, `pytest -q`. TDD (failing test first) on every deterministic
layer: the SQL trust boundary (`validate_sql`, 152 tests), the guardrail's
routing logic, bounded-recovery counting, the ambiguity backstop, the
wall-clock watchdog, degraded-mode detection, `normalize_value`. LLM
*behavior* (does the model phrase an answer well, does it choose the right
tool) is explicitly not asserted against in mocked unit tests (that's a
golden-eval concern) — instead it's verified live against the real
Anthropic + Snowflake backends at the end of each feature (documented in
`CHANGELOG.md`), and with a small set of hand-run scenarios in `evals/`
covering the PRD's own named edge cases (grounding, both guardrail
categories, ambiguity, the city redirect, the median/mean conflict, an
unanswerable query). See `docs/reflection.md` for what a full automated
eval harness would have added and why it was cut for time.

## What's cut, and why

See `docs/reflection.md` for the full account. Short version: a working,
tested, deployed core loop (agent, guardrail, bounded recovery, ambiguity
handling, degraded mode, a real chat UI) took priority over the
decennial-redistricting data source, Langfuse tracing, and a fully
automated 30-scenario eval harness with an LLM judge — all real, all
scoped and partly designed (see the closed-out GitHub issues), none
finished.
