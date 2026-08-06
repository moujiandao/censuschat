# Changelog

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
