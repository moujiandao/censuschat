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

### Changed
- Correct the metadata breadcrumb column name in schema-notes to `"FIELD_LEVELl_9"` (upstream typo, lowercase `l`)
