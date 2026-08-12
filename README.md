# censuschat

A chat agent that answers natural-language questions about US demographics,
grounded in the 2020 ACS 5-year estimates (SafeGraph's Open Census Data share
on the Snowflake Marketplace).

This README serves the two readers the assignment names: **a reviewer
evaluating the running demo** (start here) and **a new engineer learning the
architecture** (start at [Architecture](#architecture)).

---

## Evaluating the running demo

**URL:** https://censuschat.brianmar.com

| | |
|---|---|
| Username | `snowflake` |
| Password | `census` |

Health check, if you want to confirm the backend before opening the UI:

```bash
curl -u snowflake:census https://censuschat.brianmar.com/api/health
# {"status":"ok","snapshot":"ok","snowflake":"ok"}
```

`status` is `degraded` — never a crash — when the local snapshot is missing
or Snowflake is unreachable.

### Review the running system

The reviewer path is **Question → local discovery → SQL gate → Snowflake →
normalized result → answer → evidence**. Variable and geography discovery use
the local SQLite snapshot. `run_census_sql` is the one request-time Snowflake
code path, although one question can make more than one query through it.

The four tabs are **Chat, How It Works, Evidence, and Evals**. Chat contains
the four curated examples below. How It Works explains the data flow and
protection layers. Evidence shows durable SQLite-backed traces for this and
previous sessions, including raw trace JSON on demand. Evals separates the
six regression scenarios from the eight capability scenarios.

**1. Factual question**

```
Population of Harris County, Texas?
```

Then use the follow-up below. Together they demonstrate that the session
context carries the resolved geography into the next turn.

**2. Follow-up question**

```
What about households?
```

**3. Ambiguous geography**

```
How many people live in Washington County?
```

Watch for the agent to ask which Washington County is meant, rather than
silently picking one. A code-level backstop blocks `run_census_sql` if the
model attempts a query before that ambiguity is resolved (**D-014**).

**4. Unsupported request**

```
How many people will live in Texas in 2050?
```

The ACS is a measurement of the past, not a projection. The response should
explain that limitation without querying Snowflake. An off-topic question is
usually stopped earlier by the guardrail classifier.

SQL safety is code-enforced. Answer grounding is model-instructed and checked
on selected eval scenarios; this build does not independently validate every
final answer number at runtime.

Responses stream over SSE. The 50-second watchdog is a soft deadline checked
between tool-loop rounds. It prevents later calls after the deadline, but does
not interrupt a model or Snowflake call already in flight.

---

## Architecture

### Request lifecycle

```mermaid
flowchart TD
    A["Browser<br/>static/index.html"] -->|"POST /api/chat — SSE"| B["Caddy on EC2<br/>TLS + basic auth"]
    B -->|"127.0.0.1:8000"| C["FastAPI<br/>src/app.py"]
    C --> D["agent_turn<br/>src/agent.py"]
    D --> E{"Degraded?"}
    E -->|yes| Z["Honest message<br/>DONE"]
    E -->|no| F{"Guardrail<br/>Haiku"}
    F -->|REFUSE| Z
    F -->|"ALLOW — or fails OPEN"| G

    G["Tool loop<br/>8 rounds · 2 retries · 50s watchdog"] --> H["search_census_variables<br/>resolve_geography<br/>local SQLite, no network"]
    H -.-> G
    G --> J["run_census_sql"]
    J --> K{"validate_sql<br/>TRUST BOUNDARY"}
    K -.->|reject| G
    K -->|"sanitized SQL"| L[("Snowflake<br/>US_CENSUS.PUBLIC")]
    L -.->|rows| G
    G --> M["Stream tokens<br/>DONE"]

    style K fill:#b3261e,color:#ffffff
    style L fill:#2563eb,color:#ffffff
```

Two tools never leave the box: `search_census_variables` and
`resolve_geography` read local SQLite snapshots built once at startup. At
request time, **Snowflake is touched by exactly one code path**,
`run_census_sql`.

### The trust boundary

The agent has three tools and one gate. The distinction that matters:

| Layer | Kind | What happens when it fails |
|---|---|---|
| System-prompt instructions | Soft | Model may ignore them |
| Guardrail classifier (Haiku) | Soft | **Fails OPEN** — allows the turn |
| `validate_sql` (`src/sqlgate.py`) | **Hard** | Query never reaches Snowflake |

The guardrail deliberately fails open. A classifier outage must not take the
product down, and a classifier is not what makes the system safe — so it is
built to be bypassable without consequence. Everything load-bearing lives
below it, in code:

- sqlglot parse with `dialect="snowflake"` — no regex, no string matching
- exactly one statement, and it must resolve to a `SELECT` (CTEs allowed)
- every referenced table in a 31-entry allowlist, compared on the fully
  qualified rendered name so `US_CENSUS.PUBLIC."2020_cbg_b01"` does *not*
  match `US_CENSUS.PUBLIC.2020_CBG_B01`
- banned constructs rejected: DML/DDL, `INTO`, session variables, procedure
  calls, and star projection — `SELECT *` on tables averaging ~280 columns
  widens the scan regardless of `LIMIT`, so columns must be named (**D-007**);
  `COUNT(*)` is the one exemption, since it reads no column data
- **zero table references is itself a rejection**, not a no-op. That is the
  shape every read-without-naming-a-table escape takes —
  `TABLE(RESULT_SCAN(...))`, `SYSTEM$` functions, `INFORMATION_SCHEMA`
  helpers. A census answer always reads a census table.
- a table position sqlglot cannot resolve to an identifier is unverifiable
  and therefore denied, rather than waved through
- `LIMIT` injected when absent; `STATEMENT_TIMEOUT_IN_SECONDS` set on the
  session

User text is never interpolated into SQL. An allowlisted `FROM` clause does
not launder the rest of a statement — the gate inspects every table node, not
just the first. `validate_sql` carries 175 of the suite's tests for this
reason.

Answer grounding is a model instruction, not a serving-time numeric validator.
The deterministic eval harness checks numeric evidence only where its bounded
trace summary can see the relevant rows.

### Everything else

- **Session state** is full-history replay from SQLite keyed by `session_id`
  (`src/sessions.py`) — no summarization, no vector store. Multi-turn context
  survives a page reload.
- **Two independent bounds on the tool loop**, worth not conflating:
  `MAX_RECOVERY_RETRIES = 2` limits *retries after a failure* (a SQL error or
  zero rows → re-search or rewrite, then an honest failure explaining what was
  tried), while `_MAX_TOOL_LOOP_ITERATIONS = 8` caps *total rounds* even when
  every call succeeds. The second is what a genuinely multi-step question runs
  into. See the `PM-08` capability row under [Testing and evals](#testing-and-evals).
  The 50-second watchdog is a soft, between-round deadline, not an interrupt
  for work already in flight.
- **Degraded mode**: Snowflake reachability is checked once at boot and
  cached (**D-015**), because rule 13 forbids the request path from probing
  Snowflake. A snapshot-missing or Snowflake-down boot still serves 200s.
- **No agent frameworks.** Anthropic SDK + FastAPI + sqlglot +
  snowflake-connector-python. Models pinned in one module
  (`src/model_config.py`): Sonnet for the agent, Haiku for the classifier.
- **Interface contract** is `src/contracts.py`, treated as frozen. Decisions
  and interpretation calls are logged in `docs/decisions.md`.

`docs/01-architecture.md` and `docs/plans/02-prd.md` are the pre-code design
documents, left as written and marked where the build superseded them.

### What each tab shows

The frontend is one static HTML file, vanilla JS, CDN-free, no build step.

| Tab | Shows |
|---|---|
| **Chat** | The agent itself. SSE token streaming, a tool-status line, session id persisted in `localStorage`. |
| **How It Works** | The request flow, code-enforced SQL boundary, local SQLite versus Snowflake split, result normalization, and source limits. |
| **Evidence** | The one trace view: ordered guardrail, model, and tool spans from the durable SQLite trace store, with a cross-session picker and raw JSON disclosure (**D-023**, **D-027**). |
| **Evals** | The latest committed benchmark, split into six regression scenarios and eight informational capability scenarios with pass, fail, and inconclusive results. |

---

## Documented interpretations

The assignment leaves several decisions open and asks that interpretations be
documented. Full reasoning in `docs/decisions.md`; the ones that change what
the agent can answer:

**2020 vintage only.** Both a 2019 and a 2020 vintage exist in the schema.
The allowlist admits only 2020 (**D-003**). Block groups were redrawn for the
2020 decennial and the 5-year windows overlap in 4 of 5 years, so
cross-vintage comparison is statistically invalid. Restricting the allowlist
makes the invalid query *impossible at the trust boundary* rather than merely
discouraged in a prompt. Cost accepted: "how did X change since 2019" can
only ever receive an explanation, never a number.

**State and county grain, with an explicit city redirect.** Every table in
this share is at census-block-group grain; there are no place, ZIP, or metro
boundaries. State and county roll-ups are computed by truncating the 12-char
`CENSUS_BLOCK_GROUP` key. For a city question the agent says so plainly and
offers the containing county as a substitute the user must confirm — never
silently equating "Austin" with the larger Travis County (**D-005**).

**Complete coverage of the estimate set, with two deliberate exclusions.**
The assignment's "Comprehensive Mapping" tip warns against limiting yourself
to a subset, and the search corpus is indeed complete *with respect to the
estimates a user can ask about*: all 3,782 estimate fields across 298 ACS
tables and 28 topic groups, plus the metadata and FIPS join tables the tip
specifically calls out. Two classes are excluded from **retrieval** (not from
the database) because indexing them makes answers worse:

- **Margin-of-error (`m`) fields.** Estimates and MOEs pair 1:1, share a
  numeric type, and carry near-identical labels. An indexed MOE row is a
  retrieval hit that *reads like the answer* — asking for "median household
  income" could return `B19013m1`, a confidence-interval half-width, rendered
  as a dollar figure. MOE remains fully reachable: the agent derives the `m`
  column from a resolved `e` column by suffix substitution. It simply cannot
  *search* its way to one (**D-008**).
- **`B99*` allocation tables.** These report how many responses were
  *imputed*, not the underlying demographic. In the FTS viability probe,
  "grandparents raising grandchildren" returned `B99102` "Allocation Of
  Grandparents Living With Grandchildren" as the top hit — an imputation-rate
  statistic masquerading as a demographic answer.

Both exclusions follow the same principle: a plausible-looking wrong variable
is worse than no hit. That principle is also why retrieval is FTS5 + BM25
rather than embeddings — a documented probe found the gap was
morphological, not semantic ("worked at home" vs. the Census's "Worked from
home"), which ranking fixes and embeddings would solve at higher cost. See
`docs/schema-notes.md` Appendix A.

Where the data genuinely lacks a variable — grandparents raising
grandchildren, unmarried-partner households — the correct answer is that this
dataset doesn't carry it, not a near-miss substitute.

---

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in credentials
uvicorn src.app:app --reload
```

Open http://localhost:8000/. First boot builds the local variable/geography
snapshot from Snowflake (a few seconds); later boots reuse
`data/snapshot.sqlite3`. `scripts/check_env.py` verifies your environment
before you start.

`.env.example` documents every variable with no values (secrets never enter
the repo).

| Variable | Required | Notes |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | yes | |
| `SNOWFLAKE_USER` | yes | |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | yes | Key-pair auth; the file itself is never committed. **Locally this is a host path; in the deployed container it is the mount target `/run/secrets/snowflake_key.pem`** — see the compose table below |
| `SNOWFLAKE_WAREHOUSE` | yes | |
| `SNOWFLAKE_ROLE` | yes | |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | no | Only if the key is encrypted |
| `SNOWFLAKE_DATABASE` | no | |
| `SNOWFLAKE_SCHEMA` | no | |
| `ANTHROPIC_API_KEY` | yes | Agent and guardrail |
| `SNAPSHOT_DB_PATH` | no | Defaults to `data/snapshot.sqlite3` |
| `SESSION_DB_PATH` | no | Defaults to `data/sessions.sqlite3` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | no | Reserved and unread by application code; full Langfuse integration is not implemented (**D-021**) |

Three further variables exist **only for `docker-compose.yml`** and are never
read by application code, so they won't appear if you go looking for them in
`src/`:

| Variable | Used by | Notes |
|---|---|---|
| `SNOWFLAKE_PRIVATE_KEY_HOST_PATH` | compose | Host path to the `.pem`, used as the bind-mount *source*. Distinct from `SNOWFLAKE_PRIVATE_KEY_PATH`, which is the path *inside* the container |
| `BASIC_AUTH_USER` | Caddy | |
| `BASIC_AUTH_HASH` | Caddy | bcrypt hash from `caddy hash-password --plaintext '<password>'`. Caddy never sees the plaintext |

If `SNOWFLAKE_PRIVATE_KEY_HOST_PATH` is unset, `docker compose` fails with
`invalid spec: :/run/secrets/snowflake_key.pem:ro: empty section between
colons` — an unhelpful message for a simple missing variable.

> Avoid `docker compose config` on a populated host: it inlines `env_file`,
> printing every secret in `.env` as plaintext.

---

## Testing and evals

**`make test`** covers deterministic layers: the SQL trust boundary,
guardrail routing, bounded recovery, ambiguity handling, the soft watchdog,
degraded mode, FTS ranking, result normalization, and the eval scorer.

LLM *behavior* is deliberately not asserted in mocked unit tests. That is a
golden-eval concern, and the split is load-bearing: the single worst bug in
this project — every live query failing on Snowflake identifier casing —
passed the entire mocked suite and was only caught against the real database.

**`make eval`** runs the 14-scenario committed benchmark against the real
Anthropic, Snowflake, and guardrail stack. It writes a full timestamped
`EvalRun` and `latest.json` under `evals/results/`, including red rows. It is a
paid live-call command, not part of the unit suite. `evals/README.md` defines
the regression gate, informational capability evidence, tri-state semantics,
and manual CI command.

---

## Deploying

Single container on EC2 behind Caddy (TLS + basic auth). From that host, with
`.env` populated:

```bash
./deploy.sh   # git pull --ff-only && docker compose up -d --build app, waits for /api/health
```

**Caddy is native on that host, not containerized** — it also serves
`memory.brianmar.com`, so it can't be replaced by the bundled `caddy` service
without taking that site down. `deploy.sh` therefore starts only the `app`
service, and `docker-compose.override.yml` publishes it on `127.0.0.1:8000`,
which the host's existing Caddyfile already reverse-proxies to with
`flush_interval -1` for SSE. Binding to loopback rather than `0.0.0.0` means
the app is unreachable except through Caddy, so basic auth can't be bypassed
by hitting the port directly. This deviates from rule 18 and is recorded as
**D-016**.

Two things are deliberately absent from git and must exist on the host:
`.env`, and the Snowflake private key. The key must be readable by the
container's `appuser` (uid 999) — if it's owned by `ubuntu` with mode `600`
the app boots *degraded* with a `PermissionError` in `docker compose logs
app`, rather than crashing.

---

## What's cut, and why

Full account in `docs/reflection.md`. The decennial-redistricting source is
still cut (**D-004**), and no calibrated LLM judge exists for subjective answer
quality. Langfuse and prompt caching are not implemented. Evidence is instead
the durable local SQLite trace store, not a replacement for Langfuse (**D-021**,
**D-023**).

---

<!-- BEGIN id-reference (generated by scripts/build_id_reference.py) -->

## What the ids on this page mean

Eval scenarios. **live** runs today, so the question shown is the one
`make eval` actually asks. **designed** was specified but never built.
**retired** existed once and was deleted.

| id | status | question |
|---|---|---|
| `PM-08` | live | "What's the average household income in Texas?" |

Decisions, recorded in full in [`docs/decisions.md`](docs/decisions.md).

| id | decision |
|---|---|
| `D-003` | `ALLOWED_TABLES` is 2020-vintage only |
| `D-004` | Decennial redistricting tables included, phased to M3 |
| `D-005` | City/place questions get an honest redirect |
| `D-007` | Star projection rejected at the SQL gate |
| `D-008` | Variable search indexes estimate fields only |
| `D-014` | Ambiguous geography gets a code-enforced backstop, not prompt-only trust |
| `D-015` | Snowflake reachability checked once at startup, not live per-request |
| `D-016` | Native Caddy on the deploy host, not Compose's |
| `D-021` | Langfuse cut; the span model shipped in-process |
| `D-023` | Trace history is durable, and has no per-session cap |
| `D-027` | The reviewer interface has four ordered surfaces |

<!-- END id-reference -->
