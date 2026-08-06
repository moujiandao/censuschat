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

**Status:** deviation from a locked section. **Needs approval.**

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
