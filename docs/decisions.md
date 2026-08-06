# Decisions & deviations

Entries record departures from locked decisions in `docs/01-architecture.md`,
or resolutions of items it marked PROVISIONAL. Per CLAUDE.md, a deviation
requires an entry here plus Brian's explicit approval.

---

## D-001 — Dataset is SafeGraph, not Cybersyn (2026-08-05)

**Status:** resolved at M0. Architecture §12 listed "Verify Cybersyn-lineage
assumption" as the decision rule.

`SHOW DATABASES` reports the share origin as
`SAFEGRAPH.SNOWFLAKE_MANAGED$PUBLIC_AWS_US_EAST_2.SG_OPEN_CENSUS_DATA_...`.
The dataset is SafeGraph's *Open Census Data*: ACS 5-year data repackaged at
census-block-group grain, bundled with SafeGraph's own foot-traffic table.

**Consequence:** block-group-only grain; roll-up is arithmetic on a 12-char
FIPS string; no place/CBSA/ZCTA geography exists.

---

## D-002 — Two architecture §7 eval exemplars are void (2026-08-05)

**Status:** deviation from a locked section. **Approved by Brian at M1.**

Architecture §7 names two exemplars that assume geography this dataset does
not have:

1. *"ambiguous — 'How many people live in Springfield?' (expect clarifying
   question)"*. Springfield is a **place**. `2020_METADATA_CBG_FIPS_CODES`
   carries only `STATE, STATE_FIPS, COUNTY_FIPS, COUNTY, CLASS_CODE` — no
   place identifier exists anywhere in the 73 objects. The agent cannot
   resolve any city, so this cannot be an ambiguity test.
2. *"partial — a variable that exists at county but not tract level"*. Every
   ACS variable here is block-group grain and rolls up uniformly to tract,
   county, and state. No variable has partial geographic coverage, so this
   case does not exist in the data.

**Replacements, both grounded in verified dataset facts:**

- Ambiguity → **county-name collisions.** "Washington County" appears in 30
  states, "Jefferson County" 25, "Franklin County" 24. This is a stronger
  test than Springfield because the collision is real and measurable.
- Partial match → **medians cannot be aggregated.** 28 median table numbers
  exist; averaging block-group medians to county or state is invalid, and
  this share carries no published county/state medians. The honest answer
  offers the true mean from `B19025`/`B11001` instead, stating the
  substitution.

Recorded in `docs/plans/02-prd.md` §7 as AMB-01/02/03 and PM-01/02/03.

---

## D-003 — `ALLOWED_TABLES` is 2020-vintage only (2026-08-05)

**Status:** approved by Brian at M1.

Both vintages are complete, but block groups were redrawn for 2020 and the
5-year windows overlap 4 of 5 years, so cross-vintage comparison is
statistically invalid. Restricting the allowlist makes the invalid query
impossible at the trust boundary rather than merely discouraged in the
prompt.

**Cost accepted:** "how did X change since 2019" can only ever receive an
explanation, never a number.

---

## D-004 — Decennial redistricting tables included, phased to M3 (2026-08-05)

**Status:** approved by Brian at M1.

The assignment explicitly requires handling *conflicting* questions, and the
ACS-only dataset supplies no genuine source of conflict. Adding the
full-count decennial tables means "population of Travis County" has two
defensible answers — a 5-year estimate and a full count — which the agent
must surface and explain. Also demonstrates handling a union-incompatible
second metadata schema.

Phased to M3 so the M2 tracer bullet stays minimal. Requires contracts
change C-2 (`VariableHit.source`).

---

## D-005 — City/place questions get an honest redirect (2026-08-05)

**Status:** approved by Brian at M1.

No city boundaries exist in the source. The agent states this and offers the
containing county as an explicit substitute the user must accept. It never
silently equates a city with its county — Austin is roughly 40% of Travis
County, and presenting the county number as the answer would be quietly
wrong.

---

## D-006 — Architecture doc path (2026-08-05)

Minor: the architecture doc lives at `docs/01-architecture.md`, not
`docs/plans/01-architecture.md` as referenced in the original build brief.
The PRD is at `docs/plans/02-prd.md` as specified.

---

## D-007 — Star projection rejected at the SQL gate (2026-08-05)

**Status:** refinement, not a deviation — no approval required. Architecture
§4 says "banned constructs rejected" without enumerating them; this fills in
a member. Recorded here because it changes what the gate rejects.

`SELECT *` / `SELECT t.*` now fails `validate_sql`, mapped to the existing
`SqlViolation.BANNED_CONSTRUCT` rather than a new enum member, so
`contracts.py` stays frozen (CLAUDE.md rule 12).

Three properties compose into a failure none has alone: the B/C tables
average ~280 columns (8,164 field codes over 29 tables); Snowflake is
columnar, so projection width *is* scan cost; and the gate injects
`LIMIT 200` and passes the query. `SELECT * FROM ..."2020_CBG_B01"`
therefore returns ~56,000 cells into the model context — a query that
defeats both the row limit and the scan budget while passing validation.

**The generalizable point:** `SQL_ROW_LIMIT` protects tokens, the projection
rule protects scan cost. They are different resources, and a gate that
bounds one while ignoring the other is only half a gate.

**Cost accepted:** none identified. No legitimate census question needs an
unbounded projection over a 280-column table, and column-level exploration
is served by the local snapshot rather than Snowflake.

---

## D-008 — Variable search indexes estimate fields only (2026-08-05)

**Status:** refinement, not a deviation — no approval required. Architecture
§4 specifies FTS5 "over variable label+description" without specifying which
rows are indexed. Recorded here because it changes what the agent can find.

`m`-suffixed margin-of-error rows are excluded from the FTS corpus, joining
the existing `B99*` exclusion. With both filters the indexed corpus is
~3,300 rows rather than 8,164.

Estimate and MOE columns pair exactly 1:1 (schema-notes §4: 4,060 of each in
2019), are both `NUMBER(38,0)`, and carry near-identical labels. An indexed
MOE row is therefore a retrieval hit that reads like the answer: a user
asking for "median household income" could receive `B19013m1`, a 90%
confidence-interval half-width, rendered as a dollar figure. This is the
same class of failure as the `B99*` allocation tables surfacing in FTS
probe 2 (Appendix A) — a plausible-looking wrong variable, which is worse
than no hit.

**Cost accepted:** none material. MOE remains fully reachable — the agent
derives the `m` column from a resolved `e` column by suffix substitution.
It simply cannot *search* its way to one. If margin-of-error reporting
later becomes a first-class feature, it is a schema-card rule, not a
retrieval problem.

---

## D-009 — Three contracts changes applied to the interface freeze (2026-08-05)

**Status:** approved by Brian at M1. `src/contracts.py` edited under
CLAUDE.md rule 12.

All four PROVISIONALs resolved in the same pass — `ALLOWED_TABLES` (31
tables), `GeoLevel` (5 members), `SENTINEL_CODES` (verified empty),
`DEFAULT_VINTAGE` (2020). Evidence and citations in `docs/plans/02-prd.md`
§3. Beyond those, three changes altered the interface itself:

**C-1 — `CensusValue.top_coded: bool`.** `MAX(B19013e1) = 250001` is the
Census "$250,000 or more" top-code, with 776 CBGs sitting exactly there.
A top-code is a *real value carrying special meaning*, which is a different
thing from suppression — so it needed its own flag rather than being folded
into `suppressed`. Added `TOP_CODES: dict[str, float]` alongside it, keyed
by table number.

**C-2 — `VariableHit.source: Literal["acs", "decennial"]`.** Required by
D-004. Defaults to `"acs"`, so nothing changes until the redistricting
tables land at M3.

**C-3 — `VariableHit.geo_levels` reinterpreted as aggregation validity.**
No signature change; semantics only. The field was dead under the
availability reading, because every ACS variable in this share is available
at all five levels via roll-up, making it a constant. Under the validity
reading, count variables carry all five levels and the 28 median tables
carry `[BLOCK_GROUP]` only.

**Why C-3 is the load-bearing one:** the most likely wrong answer this
system can produce is a median averaged up to county or state. That error
is invisible — it returns a plausible number with no error and no empty
result. Encoding the rule in a field the retrieval layer must populate makes
it a data property that tests can assert on, rather than a sentence in a
prompt that the model may or may not honor.

**Cost accepted:** `normalize_value` gains an optional `variable_id`
parameter to look up top-codes. That is a signature widening, not a break —
existing single-argument calls still work.

---

## D-010 — `validate_sql`: six gate behaviors beyond issue #1's spec (2026-08-06)

**Status:** refinement, not a deviation — no approval required, same class
as D-007/D-008. Recorded because it changes what the gate rejects, and
because every one of the six trends strictly toward *more* restrictive —
never toward permissiveness, which is the correct default whenever the
spec is silent on a security boundary (CLAUDE.md rule 5).

Call 1 is a genuine spec gap. Calls 2–3 are default-deny hardening.
Calls 4–6 close real bypasses — each one verified empirically against the
actual `validate_sql` function, not inferred from reading the code: before
the fix, each example below returned `ok=True` with zero violations.

1. **An explicit `LIMIT` above `SQL_ROW_LIMIT` is clamped down, not
   preserved.** Issue #1 specifies preserving a smaller explicit `LIMIT`
   and is silent on a larger one. Honoring `LIMIT 100000` would turn the
   cap into a default rather than a bound, and let the model lift its own
   ceiling by asking for it.
2. **A statement referencing zero tables is `TABLE_NOT_ALLOWED`.** Table
   functions (`SELECT SYSTEM$...()`, `TABLE(RESULT_SCAN(...))`) produce no
   `Table` node, so an allowlist that only walks table references would
   never run against them. Every legitimate census answer reads a census
   table, so the empty case is rejected by default.
3. **`OBJECT_CONSTRUCT(*)` / `ARRAY_CONSTRUCT(*)` / `HASH(*)` are rejected
   alongside `SELECT *`; `COUNT(*)` is the sole exemption.** D-007's star
   rule is stated over the star token itself, not over the outermost
   projection list — so any construct that reads all columns is banned,
   and the one star that reads no columns (`COUNT(*)`) is let through.
4. **Function calls sqlglot cannot model (`exp.Anonymous`, plus
   `IDENTIFIER()`) are rejected.** An allowlisted `FROM` clause does not
   launder the rest of the statement:
   `SELECT a, SYSTEM$CANCEL_ALL_QUERIES() FROM <allowed>` (a side effect —
   a write wearing a SELECT's clothes) and
   `SELECT GET_DDL('TABLE', '..."2019_CBG_B99"'), a FROM <allowed>` (names
   its real target with a string literal, so it produces no `Table` node)
   both passed with zero violations before this. `IDENTIFIER()` is the
   same hazard — a string-built object reference — and is named
   explicitly. A denylist cannot cover UDFs or external functions, whose
   names are arbitrary and could post the rows they're handed to any
   endpoint; "sqlglot has no typed node for this" can, on the same
   default-deny logic as everything else here. **Measured cost:** of 48
   functions a census answer plausibly needs, 47 are typed nodes — `DIV0`
   and `ZEROIFNULL` included, since sqlglot's Snowflake dialect desugars
   both to `IFF`/`Is`/`Div` primitives at parse time rather than leaving
   them as function calls (verified directly: neither ever reaches
   `_unmodeled_functions`). Only `RATIO_TO_REPORT` is rejected.
5. **CTE names resolve by lexical scope, not a flat set collected from the
   whole statement.** A decoy CTE named after a forbidden table, defined
   in a branch where it isn't in scope, was excusing a bare reference to
   the real 2019 table in a sibling branch — defeating D-003, the
   allowlist's entire purpose.
6. **CTE scope also respects declaration order within one `WITH` list.** A
   CTE cannot see one declared after it (except under `WITH RECURSIVE`,
   which may see itself). Same bypass as #5, one level down: a CTE
   declared *after* the one referencing its name doesn't shadow a bare
   reference to the real table — Snowflake resolves that reference against
   the physical table, and the gate now does too. Verified `WITH
   RECURSIVE`'s legitimate self-reference still passes when the query also
   reads a real allowed table (isolated from call #2's zero-table rule,
   which otherwise masks the recursive case in a query that reads nothing
   else).

**Notes for whoever implements `run_census_sql` (issue #5):**
`SqlGateResult.sql` is sqlglot's regenerated SQL, not the model's original
text byte-for-byte — `DIV0(a,b)` becomes `IFF(...)`, `--` comments become
`/* */`. This is a safe property (no room for an injected comment to
survive re-serialization) but means don't expect it to echo verbatim.
`SqlGateResult.sql` is `""` on every rejection, by design.

All six calls, and their reasoning, are recorded in the implementation
commits (`924ece2`, `ed17956`) as well as here. Calls 4–6 were found by
continued adversarial self-review after the issue's stated exit criteria
were already met and the first commit was closed — each was verified
empirically before being called fixed, both by the implementing agent and
independently against the current code (see `CHANGELOG.md`).

---

## D-011 — Median-variable detection is unverified against live data (2026-08-05)

**Status: flagged assumption, not yet empirically verified. Needs a live
Snowflake check before the `conflicting`/`direct_fact` golden evals involving
a median table can be trusted.**

D-009/C-3 requires `VariableHit.geo_levels` to return `[GeoLevel.BLOCK_GROUP]`
for the ~28 median-table variables and all five levels otherwise — the
mechanism that prevents a median from being silently (and wrongly) averaged
up to county/state. `src/tools.py:_geo_levels_for` implements this by
checking whether `"median"` (case-insensitive) appears in the variable's
`TABLE_TITLE`, on the assumption that ACS median-variable titles reliably
start with "Median ..." by Census naming convention.

**Why an assumption instead of a verified list:** no live Snowflake
connection was available in the session that built `src/tools.py` (issues
#3/#4/#5), so the actual 28 table numbers referenced in D-009/PRD §3 could
not be pulled and hardcoded the way `ALLOWED_TABLES`/`TOP_CODES` were.
Rule 12 wants PROVISIONAL items resolved from schema-notes evidence, not
assumption — this is exactly that gap, made explicit here rather than
silently shipped.

**Failure mode if wrong:** a false negative (a median table whose title
doesn't contain "median") would incorrectly get all five geo_levels and
could pass a median through the exact silent-wrong-average bug C-3 exists to
prevent. A false positive (a non-median table containing "median" in its
title for an unrelated reason) would only over-restrict — annoying, not
wrong.

**Next step:** run `TABLE_TITLE` against the real
`2020_METADATA_CBG_FIELD_DESCRIPTIONS` table and confirm the substring
heuristic's precision/recall against the actual 28-table list before relying
on it for the `make eval` golden set.
