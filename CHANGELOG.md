# Changelog

## [2026-08-06]

### Added
- Add `src/sqlgate.py` implementing `validate_sql`, the SQL trust boundary (issue #1, CLAUDE.md rules 1/5). TDD, 152 tests written first. Three gate behaviors beyond the issue's literal spec, all strictly more restrictive, recorded as D-010: LIMIT above the cap is clamped rather than preserved, zero-table statements are rejected, and `OBJECT_CONSTRUCT(*)`/`ARRAY_CONSTRUCT(*)`/`HASH(*)` are banned alongside `SELECT *` with `COUNT(*)` exempted
- Add `src/app.py` and `src/agent.py` (placeholder pending issue #7): `POST /api/chat` streams `ChatEvent`s as SSE, every path terminates with `done` or `error`, mid-turn exceptions become an honest `error` event rather than a raw 500 (issue #6, CLAUDE.md rule 11)
- Add `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `deploy.sh`, `.dockerignore` (issue #9): Caddy reaches the app by compose service name; basic auth via bcrypt hash, plaintext only in `.env`/README; SQLite persistence via a named volume; deploy script polls `/api/health`
- Add root `conftest.py` so `src/` resolves as a PEP 420 namespace package under pytest with no packaging step

### Fixed
- Escape control characters in `session_id` before logging (`src/app.py`) — an unvalidated client-supplied newline could otherwise forge fake log lines
- Run the app container as a non-root user; verified the `chown`'d `/app/data` ownership survives being overlaid by a fresh named volume, and that the app starts and serves correctly under the new user

### Fixed (continued)
- Close two further `validate_sql` bypasses found by continued adversarial review after issue #1's stated criteria were already met: unmodeled function calls (`SYSTEM$CANCEL_ALL_QUERIES()`, `GET_DDL(...)`, `IDENTIFIER()`) laundering a side effect or a string-named table reference through an allowlisted `FROM` clause, and CTE name resolution that ignored both nesting scope and declaration order — either of which let a decoy or forward-declared CTE name excuse a bare reference to the real forbidden 2019 table. All four were `ok=True` with zero violations before the fix; all four independently re-verified after it, along with a fifth check confirming `WITH RECURSIVE`'s legitimate self-reference still passes. D-010 rewritten to document all six judgment calls (was three) plus two notes for `run_census_sql` (issue #5): the gate's sanitized SQL is sqlglot's regenerated text, not the model's original verbatim, and is `""` on every rejection

### Verified
- Full 179-test suite passes both on local Python 3.14 and inside the actual `python:3.13-slim` production container image — closes a build-vs-dev interpreter mismatch flagged in review
- `(SELECT ...) LIMIT N` (the gate's output for a bare parenthesized query, an untested parse-tree shape) is valid Snowflake syntax and genuinely bounds the row count — confirmed against the real warehouse, returned exactly 200 rows; added as a permanent regression test
- `DIV0`/`ZEROIFNULL` are not rejected as unmodeled functions, contradicting an independent reviewer's static-analysis finding — sqlglot's Snowflake dialect desugars both into typed `IFF`/`Is`/`Div` primitives at parse time, so they never reach the unmodeled-function check. Confirmed by inspecting the actual parse tree, not by re-reading source
- Ran a full code-review pass (code-reviewer subagent) over `src/sqlgate.py`, `src/app.py`, and the deploy scaffold before pushing; one finding (missing `.env.example`) was a false positive — the file is tracked and pushed, the reviewer's `Glob` missed a root-level dotfile
- A concurrent background agent's `git commit --amend` briefly folded an unrelated changelog commit into its own; caught before push via `git reflog`, and already self-corrected by the agent (`git reset --soft` + a cleanly separate commit) — no work lost, confirmed by diffing the discarded amend against the final state

## [2026-08-05]

### Added
- Add `scripts/sf_query.py`, a key-pair-auth CLI over snowflake-connector-python for recon (tooling, not application code)
- Add `requirements.txt` pinning snowflake-connector-python and python-dotenv
- Add `.env.example` documenting all eight Snowflake variables with no values
- Add `.gitignore` covering `.env`, `.venv/`, `*.pem`, `*.p8`
- Add `docs/schema-notes.md` from the schema-recon subagent: 73-object inventory, block-group grain and roll-up recipes, join keys, metadata structure, ACS vintage, and verified traps
- Add `docs/schema-notes.md` Appendix A recording the FTS-viability probe
- Add `docs/plans/02-prd.md` per architecture §15, with all four PROVISIONALs resolved and three contracts changes flagged
- Add `docs/decisions.md` recording six decisions and deviations
- Add PRD §6.1 specifying prompt caching — one `cache_control` breakpoint on the last system block, because the render order `tools` → `system` → `messages` means a single breakpoint covers the tool schemas and schema card together, and the 3–5 model calls per turn each resend that ~2K-token prefix; caching cuts it from 4× to 1.55× and so pays inside one turn rather than across a session
- Add PRD §4.3 correctness rule 5 (name every column; always aggregate or bound) because `LIMIT 200` is a backstop against a runaway payload, not a target to fill — real questions resolve to 1 row, 2–5 for a comparison, 10 for a top-N
- Add snapshot persistence across restarts via a Docker volume, because ACS vintage data is immutable so there is nothing to invalidate, and `build_snapshot(force=False)`'s existing no-op only holds if the SQLite file outlives the container; also narrows degraded mode to "cache missing *and* Snowflake unreachable" instead of every reboot while the warehouse is asleep
- Add decision D-007 (star projection) and D-008 (estimate-only search corpus) to `docs/decisions.md`
- Add PRD §12 risk entries for the Cloudflare-in-front-of-Caddy topology: architecture §13 assumed a single proxy, but Cloudflare is the layer that buffers, and a buffered SSE stream reads as a hang then a wall of text — failing the interactivity requirement in the deployed environment only; M2's "first token visible" exit criterion now must be verified through Cloudflare, not just Caddy
- Add PRD §12 note that Cloudflare TLS termination can break Caddy's HTTP-01 challenge, requiring Full (strict) with an origin cert or a switch to DNS-01
- Add PRD §12 rationale for retaining basic auth (CLAUDE.md rule 18, no deviation): an unauthenticated endpoint calling Anthropic and Snowflake per request is an unbounded cost exposure, with credentials at the top of the README so reviewers are never blocked
- Add `scripts/check_env.py`, which authenticates every credential in `.env` and prints PASS/FAIL without ever printing a secret value; uses `models.list()` for the Anthropic check so verification costs zero tokens. Serves the M6 clean-env exit criterion as well as local use
- Add 5 GitHub milestones (M2–M6) and 28 issues, each carrying Context, Exit criteria, and a TDD test list per CLAUDE.md rule 19

### Changed
- Resolve all four `src/contracts.py` PROVISIONALs: `ALLOWED_TABLES` (31 tables, 2020 only), `GeoLevel` (5 members), `SENTINEL_CODES` (verified empty), `DEFAULT_VINTAGE` (2020)
- Remove `PLACE`, `CBSA`, `ZCTA` from `GeoLevel` — no place, CBSA, or ZCTA identifier exists anywhere in the 73 objects and there is no crosswalk to derive one
- Add `CensusValue.top_coded` and `TOP_CODES` (C-1): a Census top-code is a real value carrying special meaning, distinct from suppression, so it needed its own flag rather than being folded into `suppressed`
- Add `VariableHit.source` defaulting to `"acs"` (C-2), so nothing changes until the redistricting tables land at M3
- Reinterpret `VariableHit.geo_levels` as aggregation validity rather than availability (C-3), encoding the median-rollup trap as a data property tests can assert on instead of a prompt sentence the model may ignore
- Widen `normalize_value` with an optional `variable_id` for top-code lookup (non-breaking)
- Correct the metadata breadcrumb column name in schema-notes to `"FIELD_LEVELl_9"` (upstream typo, lowercase `l`)
- Reject star projection (`SELECT *`) at the SQL gate, mapped to the existing `SqlViolation.BANNED_CONSTRUCT` so `contracts.py` stays frozen — the B/C tables average ~280 columns and Snowflake is columnar, so `SELECT *` is a full-width scan that the injected `LIMIT 200` then turns into ~56,000 cells of model context; the row limit protects tokens and the projection rule protects scan cost, and bounding only one of them left the other open (D-007)
- Exclude `m`-suffixed margin-of-error rows from the FTS corpus, taking it from 8,164 to ~3,300 indexed rows, because estimate and MOE columns pair 1:1 with near-identical labels, so an indexed MOE row is a retrieval hit that reads like the answer — "median household income" could return `B19013m1`, a confidence-interval half-width, rendered as a dollar figure (D-008)
- Specify the geography snapshot as `2020_METADATA_CBG_FIPS_CODES` (~3.3K rows) explicitly, not `2020_METADATA_CBG_GEOGRAPHIC_DATA` (242,335 rows), because the phrase "geography index" admitted a 68×-larger reading that answers no question a user asks by name; the larger table stays allowlisted for Snowflake-side density queries but is never snapshotted
