# censuschat

A chat agent that answers natural-language questions about US demographics,
grounded in the 2020 ACS 5-year estimates (SafeGraph's Open Census Data
share on the Snowflake Marketplace).

**Live demo:** https://censuschat.brianmar.com

**Credentials** (HTTP Basic Auth):

| | |
|---|---|
| Username | `snowflake` |
| Password | `census` |

```bash
curl -u snowflake:census https://censuschat.brianmar.com/api/health
```

`/api/health` returns `{"status":"ok","snapshot":"ok","snowflake":"ok"}`
when the app can reach both its local snapshot and Snowflake, and
`degraded` (never a crash) when it can't.

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

The app runs as a single container on an EC2 host, behind Caddy (TLS +
basic auth). From that host, with `.env` populated per `.env.example`:

```bash
./deploy.sh   # git pull --ff-only && docker compose up -d --build app, waits for /api/health
```

**Caddy is native on that host, not containerized** (it also serves
`memory.brianmar.com`, so it can't be replaced by the bundled `caddy`
service without taking that site down). `deploy.sh` therefore starts only
the `app` service, and `docker-compose.override.yml` publishes it on
`127.0.0.1:8000` — which the host's existing `/etc/caddy/Caddyfile`
already reverse-proxies to, with `flush_interval -1` set for SSE. Binding
to loopback rather than `0.0.0.0` means the app can't be reached except
through Caddy, so basic auth can't be bypassed by hitting the port
directly. This deviates from CLAUDE.md rule 18 ("Caddy reaches the app by
compose service name, never localhost") and is recorded as **D-016**.

Two things are deliberately absent from git and must exist on the host
(CLAUDE.md rule 8): `.env`, and the Snowflake private key. The key must be
readable by the container's `appuser` (uid 999) — if it's owned by
`ubuntu` with mode `600` the app boots *degraded* with a `PermissionError`
in `docker compose logs app`, rather than crashing.

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
handling, degraded mode, a chat UI with Chat/Evals/Flow Diagram/Trace
Logging tabs) took priority over the decennial-redistricting data source,
real Langfuse tracing, and the full 30-scenario eval suite with an LLM
judge — all real, all scoped and partly designed (see the closed-out
GitHub issues), none finished. The eval harness *is* real (`make eval`,
11 of the PRD's 30 golden scenarios run verbatim against the live stack,
11/11 passing), but `judge_groundedness` is unimplemented and the
`conflicting` category has no coverage — `evals/README.md` states exactly
what each check does and doesn't verify. The Trace Logging tab
(`src/tracing.py`) renders real,
per-turn span data (guardrail, model calls with token counts, tool calls
with latency) but is in-memory and in-process only — a stand-in for rule
17's actual Langfuse requirement, not a replacement for it.
