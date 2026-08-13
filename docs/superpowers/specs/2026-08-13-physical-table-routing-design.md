# Deterministic Physical Table Routing

## Problem

`search_census_variables` returns an ACS variable identifier such as
`B11012e1`, but the agent must currently infer that the identifier lives in
`US_CENSUS.PUBLIC."2020_CBG_B11"`. In the observed MT-01 turn, the model used
the logical ACS table number and requested the nonexistent physical table
`2020_CBG_B11012`. The SQL gate correctly rejected it, then bounded recovery
spent another model round producing the correct table.

This is not a SQL-gate or Snowflake lookup defect. It is a deterministic
schema mapping delegated to probabilistic model reasoning.

## Design

Add a required `physical_table: str` field to `VariableHit`. For ACS search
results, `search_census_variables` derives the SQL-ready, fully qualified,
quoted table name from the variable identifier's topic prefix:

```text
B11012e1 -> US_CENSUS.PUBLIC."2020_CBG_B11"
C15002e1 -> US_CENSUS.PUBLIC."2020_CBG_C15"
```

The agent prompt will require SQL to use `physical_table` exactly as returned.
It will no longer instruct the model to derive a physical table from
`TABLE_NUMBER`.

The field belongs in `VariableHit`, rather than being appended only to the
serialized model payload, so the codebase retains one documented tool
interface. The contract change is recorded as D-024.

## Data Flow

1. SQLite FTS returns `variable_id`, label, and universe.
2. `search_census_variables` derives `physical_table` in code while building
   each `VariableHit`.
3. `_run_tool` serializes the complete result without special-case payload
   mutation.
4. The model copies `physical_table` into `run_census_sql` SQL.
5. `validate_sql` continues to check the exact table against `ALLOWED_TABLES`.

The SQL gate remains default-deny and performs no silent correction.

## Error Handling

The derivation applies only to the current ACS variable format, whose first
three characters identify an allowlisted physical topic table. Construction
must verify the derived fully qualified name is present in `ALLOWED_TABLES`.
If snapshot data violates that assumption, variable search fails explicitly
instead of returning an unusable or unapproved table.

Future decennial search support must populate its own exact physical table.
It must not reuse the ACS prefix derivation.

## Testing

TDD will add a failing deterministic test before production changes. The test
will prove that a `B11012e1` search hit carries
`US_CENSUS.PUBLIC."2020_CBG_B11"`, not `2020_CBG_B11012`. A `C15` case will
cover the second allowlisted ACS prefix family.

After the focused test passes, run the complete offline suite with `make test`.
The live MT-01 eval is useful confirmation but is not part of routine offline
verification because it requires real Anthropic and Snowflake credentials and
incurs cost.

## Documentation

Update `CHANGELOG.md`, record D-024 in `docs/decisions.md`, and run `make docs`
so decision references remain current.

## Non-goals

- Do not add a fourth agent tool.
- Do not rewrite rejected SQL.
- Do not change the table allowlist.
- Do not add snapshot columns or force a snapshot rebuild.
- Do not add dependencies.
