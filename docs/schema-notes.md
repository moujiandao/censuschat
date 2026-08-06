# Schema Notes — US_CENSUS (Snowflake)

Explored directly in Snowflake (`SHOW`/`DESCRIBE`/`INFORMATION_SCHEMA` + sampling). Database: `US_CENSUS`, schema: `PUBLIC`. All 71 tables live in this one schema; no other schemas have data (`INFORMATION_SCHEMA` is the only other schema).

**Provenance (verified, not Cybersyn):** `SHOW DATABASES` shows `US_CENSUS` was imported from share
`SAFEGRAPH.SNOWFLAKE_MANAGED$PUBLIC_AWS_US_EAST_2.SG_OPEN_CENSUS_DATA_SNOWFLAKE_SECURE_SHARE_1638497943545`.
This is **SafeGraph's "Open Census Data"** product — Census ACS/decennial data repackaged by SafeGraph, joined at the census-block-group (CBG) grain, and shipped alongside SafeGraph's own foot-traffic "Patterns" table in the same database. It is not Cybersyn.

All identifiers below need double-quoting in Snowflake because they start with a digit or contain mixed case, e.g. `US_CENSUS.PUBLIC."2019_CBG_B01"`, and column names like `"B01001e1"` are case-sensitive (unquoted defaults to upper, which won't match).

## 1. Tables and row counts

Two vintages exist side by side: a **2019** release and a **2020** release. Nothing is deleted/replaced between vintages — both are permanent tables you must choose between.

### Demographic data tables (ACS 5-year detail tables, one physical table per topic letter+number)

Each of these 23 "B" tables and 6 "C" tables is wide (many ACS tables' fields packed as columns) and keyed one row per CBG.

| Vintage | Row count (each B/C table) | Tables |
|---|---|---|
| 2019 | 220,333 | `2019_CBG_B01,B02,B03,B07,B08,B09,B11,B12,B14,B15,B16,B17,B19,B20,B21,B22,B23,B24,B25,B27,B28,B29,B99` (23) + `C02,C15,C16,C17,C21,C24` (6) |
| 2020 | 242,335 | same 23 B + 6 C tables, prefixed `2020_` |

The row-count jump (220,333 → 242,335 CBGs) between vintages is the 2020 census block-group redraw, not missing data — block-group boundaries were redrawn for the 2020 decennial census, so CBG counts/IDs are **not stable across vintages**. Don't compare 2019 and 2020 CBG-keyed rows as if they're the same geography.

### Metadata tables

| Table | Rows | Purpose |
|---|---|---|
| `2019_METADATA_CBG_FIELD_DESCRIPTIONS` | 8,120 | field code → human label (see §4) |
| `2019_METADATA_CBG_FIPS_CODES` | 3,233 | state/county FIPS → names |
| `2019_METADATA_CBG_GEOGRAPHIC_DATA` | 220,333 | land/water area (sq m), lat/lon centroid, per CBG |
| `2020_METADATA_CBG_FIELD_DESCRIPTIONS` | 8,164 | same, 2020 vintage |
| `2020_METADATA_CBG_FIPS_CODES` | 3,234 | same, 2020 vintage |
| `2020_METADATA_CBG_GEOGRAPHIC_DATA` | 242,335 | same, 2020 vintage |

### Geometry tables

| Table | Rows | Notes |
|---|---|---|
| `2019_CBG_GEOMETRY` | 220,740 | `GEOMETRY` column is native Snowflake `GEOGRAPHY` type |
| `2019_CBG_GEOMETRY_WKT` | 220,740 | same polygons, `GEOMETRY` column is `VARCHAR` holding WKT text |
| `2020_CBG_GEOMETRY_WKT` | 242,335 | 2020 has **only** the WKT variant, no native-`GEOGRAPHY` table |

Geometry row counts don't match the demographic tables' row counts (220,740 vs 220,333 in 2019) — see §5 traps.

### Decennial 2020 redistricting data (separate from ACS)

| Table | Rows | Notes |
|---|---|---|
| `2020_REDISTRICTING_CBG_DATA` | 242,335 | PL 94-171 redistricting file: `P0010001`…`P0050xxx`-style codes (race/Hispanic-origin/voting-age counts), **not** ACS `Bxxxxx`-style codes |
| `2020_REDISTRICTING_METADATA_CBG_FIELD_DESCRIPTIONS` | 298 | field code → label, different schema than the ACS field-description table (`FIELD_NAME, COLUMN_ID, COLUMN_TOPIC, COLUMN_UNIVERSE`) |
| `2020_REDISTRICTING_METADATA_CBG_GEOGRAPHIC_DATA` | 242,335 | land/water/centroid, same shape as the ACS geographic-data table |

This is the full-count decennial census, not a sample — it has **no margin-of-error columns at all** (confirmed via `DESCRIBE TABLE`: every column is a plain `P00xxxxx` estimate, no `m`-suffixed sibling). Don't assume every table in this database follows the ACS estimate/MOE pairing.

### Non-census table (SafeGraph foot traffic — out of scope for a Census chat tool)

| Table | Rows | Notes |
|---|---|---|
| `2019_CBG_PATTERNS` | 220,735 | SafeGraph POI visit-pattern rollups per home CBG: `RAW_VISIT_COUNT`, `VISITOR_HOME_CBGS` (VARIANT/JSON), `TOP_BRANDS`, `POPULARITY_BY_HOUR`, etc. This is commercial foot-traffic data, not Census demographics. It sat in the same schema because SafeGraph ships it with the Open Census bundle. **Recommend excluding it from the table allowlist** — it's not census data and its JSON/VARIANT columns don't fit the ACS numeric-estimate model at all. |

## 2. Geography grain and roll-up

**Every demographic/geometry/metadata table is at census-block-group (CBG) grain. There are no separate tract, county, or state tables anywhere in the database.** Roll-up to tract/county/state must happen by aggregating (SUM, or population-weighted AVG for rates/medians) CBG rows grouped by a truncated FIPS prefix — the querying layer has to do this arithmetic, the data doesn't provide it pre-aggregated.

`CENSUS_BLOCK_GROUP` is always a fixed 12-character string (verified: `LEN(CENSUS_BLOCK_GROUP)` = 12 for all 220,333 rows in `2019_CBG_B01`), decomposing as:

```
positions 1-2   state FIPS      (2 digits)
positions 3-5   county FIPS     (3 digits)
positions 6-11  tract code      (6 digits)
position  12    block group     (1 digit)
```

Verified by joining `SUBSTR(CENSUS_BLOCK_GROUP,1,2)` / `SUBSTR(CENSUS_BLOCK_GROUP,3,3)` against `METADATA_CBG_FIPS_CODES.STATE_FIPS`/`COUNTY_FIPS` — e.g. `421010065004` → state `42` (PA), county `101` (Philadelphia County), tract `006500`, block group `4`. This join covers all 220,333 CBGs with zero unmatched rows.

Roll-up recipe:
- **Tract** = `SUBSTR(CENSUS_BLOCK_GROUP, 1, 11)` (drop last digit)
- **County** = `SUBSTR(CENSUS_BLOCK_GROUP, 1, 5)`
- **State** = `SUBSTR(CENSUS_BLOCK_GROUP, 1, 2)`

`METADATA_CBG_FIPS_CODES` also carries a `CLASS_CODE` per county (values seen: `H1` 3,115 counties — standard county; `H4` 29; `H5` 10; `H6` 38; `C7` 41 — consolidated city/county and independent-city equivalents). Anything summing "counties" needs to know these aren't all the same legal entity type.

## 3. Join keys between tables

- **CBG-to-CBG joins** (demographic ↔ geometry ↔ metadata ↔ patterns ↔ redistricting): `CENSUS_BLOCK_GROUP` (12-char string), exact match, same vintage only. 2019 and 2020 CBG codes are **not** comparable (block groups were redrawn — see §1).
- **CBG-to-county/state**: `SUBSTR(CENSUS_BLOCK_GROUP,1,2)` = `METADATA_CBG_FIPS_CODES.STATE_FIPS`, `SUBSTR(CENSUS_BLOCK_GROUP,3,3)` = `METADATA_CBG_FIPS_CODES.COUNTY_FIPS`.
- **Field code → label**: `METADATA_CBG_FIELD_DESCRIPTIONS.TABLE_ID` (e.g. `B01001e1`) matches a demographic table's column name exactly (case-sensitive). `TABLE_NUMBER` (e.g. `B01001`, no `e`/`m` suffix) is the ACS table number and also the prefix that tells you which physical Snowflake table (`2019_CBG_B01`) holds that column — first two digits after the letter select the file (`B01xxx`→`_B01`, `B19xxx`→`_B19`, `C15xxx`→`_C15`, etc.). Verified across ~15 table-number groups.
- Row-count mismatches mean these joins are **not guaranteed to be lossless** — see §5.

## 4. Field-description / metadata table structure

Two different metadata schemas exist depending on which dataset you're in:

**ACS tables** (`2019_METADATA_CBG_FIELD_DESCRIPTIONS`, `2020_...`), one row per field code, columns:
`TABLE_ID, TABLE_NUMBER, TABLE_TITLE, TABLE_TOPICS, TABLE_UNIVERSE, FIELD_LEVEL_1 … FIELD_LEVEL_8, "FIELD_LEVELl_9", FIELD_LEVEL_10` (nested breadcrumb of the Census table's crosstab structure, e.g. `Estimate → SEX BY AGE → Total population → Total → Male → 22 to 24 years`).

> **Upstream typo — the 9th breadcrumb column is `FIELD_LEVELl_9`, with a lowercase `l` before the `_9`** (verified via `DESCRIBE TABLE`). It is not `FIELD_LEVEL_9`. Because the name is mixed-case it must be double-quoted in Snowflake: `"FIELD_LEVELl_9"`. Referring to `FIELD_LEVEL_9` raises `invalid identifier`. The snapshot builder must hardcode the misspelling.

- `TABLE_ID` = the exact column name in the demographic table (`B01001e10`).
- `TABLE_NUMBER` = ACS table number without the estimate/MOE suffix (`B01001`).
- `TABLE_UNIVERSE` = the denominator population for that whole table (`Total population`, `Households`, `Workers 16 years and over`, `Civilian population 18 years and over`, etc.) — this is the field to check before comparing two variables (see §5).
- 364 distinct `TABLE_NUMBER`s in the 2019 vintage, spanning topics: age/sex, race, Hispanic origin, commuting, income, poverty, housing (tenure/rent/value/plumbing/rooms), health insurance, internet access, veteran status, citizenship, and ~70 `B99xxx`/`B992xxx` "Allocation Of ..." tables (imputation-flag counts, not the underlying demographic — a likely candidate to exclude from variable search, see below).
- `TABLE_ID` suffix `e<n>` = estimate, `m<n>` = margin of error, `<n>` is the field's position in the table. Confirmed 1:1 pairing: 4,060 `e` rows and 4,060 `m` rows in the 2019 metadata table, every `e` has a matching `m`.

**Decennial redistricting table** (`2020_REDISTRICTING_METADATA_CBG_FIELD_DESCRIPTIONS`) uses a *different* column layout: `FIELD_NAME, COLUMN_ID, COLUMN_TOPIC, COLUMN_UNIVERSE` (flat, no `e`/`m` suffix distinction, no `FIELD_LEVEL` breadcrumb, no MOE — full count).

Any variable-discovery/search layer needs a branch per metadata schema (ACS vs redistricting) — they are not union-compatible as-is.

## 5. ACS vintage

Confirmed via table title text: `2019_...B19013e1` is titled *"Median Household Income In The Past 12 Months (In 2019 Inflation-Adjusted Dollars)"*, `2020_...B19013e1` is *"...In 2020 Inflation-Adjusted Dollars)"*. Because CBG-level ACS data is only ever published as a **5-year rolling estimate** (the Census Bureau does not release 1-year ACS estimates below ~65k population, which excludes block groups entirely), these are:

- `2019_*` = **ACS 2015–2019 5-year estimates**
- `2020_*` = **ACS 2016–2020 5-year estimates**

Every number in these tables is a multi-year average, not a single-year snapshot — an assistant answering "how many people..." should say "based on the 2015–2019 5-year ACS estimate" rather than implying a 2019 point-in-time count.

## 6. Traps

**Margin of error columns.** Every ACS estimate column `<code>e<n>` has a paired `<code>m<n>` margin-of-error column (90% confidence interval half-width). These are **separate numeric columns you must explicitly select** — there's no flag distinguishing them other than the `e`/`m` character embedded in the column name, and both are typed identically (`NUMBER(38,0)`), so it's easy to sum/join the wrong one. The decennial redistricting table has no MOE columns at all (full count, not sampled) — don't code an assumption that "every table has an `m` sibling."

**Suppressed / null-coded values.** This loader represents suppression as actual SQL `NULL`, not a Census-API sentinel number (no `-666666666`, no `999999999` observed). Verified on `B19013e1` (median household income): 8,299 of 220,333 CBGs (3.8%) are `NULL` — these are block groups with too few households/insufficient sample to compute a reliable median, not zero income. When the estimate is `NULL`, its paired MOE column is always `NULL` too (checked: zero rows have a non-null MOE alongside a null estimate). **Treat `NULL` in any estimate column as "not reported," never coerce to 0.** Also watch for Census's own top/bottom-coding conventions baked into the estimate values themselves (e.g. median income literally capped at `250001` = "$250,000+", observed as the table max) — a value sitting exactly at the visible max/min of a "median"/"aggregate" column is often a top/bottom code, not a real median.

**Population-vs-household (and other universe) denominator mismatches.** `TABLE_UNIVERSE` in the field-description metadata varies per table: `Total population`, `Households`, `Families`, `Housing units`, `Workers 16 years and over`, `Civilian population 18 years and over`, `Occupied housing units`, etc. Mixing numerators/denominators across universes silently produces nonsense rates. Concretely verified: 462 of 220,333 CBGs (2019) have `B01003e1` (total population) > 0 but `B11001e1` (households) = 0 — these are block groups dominated by group-quarters population (dorms, prisons, nursing homes, barracks) who count as population but not as any household, so "population" and "households" are not interchangeable denominators even before you get to fancier tables. Always check `TABLE_UNIVERSE` before dividing one table's estimate by another's.

**Row counts don't match across joined tables — joins can silently drop or duplicate.** In the 2019 vintage: demographic tables have 220,333 CBG rows; `GEOMETRY`/`GEOMETRY_WKT` have 220,740 (407 more — polygons for CBGs with zero measured population, e.g. water/uninhabited areas, that never got a demographic row); `PATTERNS` has 220,735 (missing 2 CBGs that the demographic tables do have, since SafeGraph's foot-traffic coverage isn't 100%). An inner join between demographic and geometry tables silently drops 407 real block groups; an inner join with `PATTERNS` silently drops 2. Any geography answer that depends on `PATTERNS` inheriting full demographic coverage (or vice versa) needs an explicit outer join / coverage check, not an assumed 1:1.

**Non-census data in the same schema.** `2019_CBG_PATTERNS` is SafeGraph commercial foot-traffic data (visit counts, popular brands, hourly popularity), not Census-sourced. It happens to share the `CENSUS_BLOCK_GROUP` key and the schema, so a naive "search all tables" variable-discovery approach would surface it as if it were a census variable. Exclude it from the table allowlist for a Census chat tool.

**`B99*` / `B992*` "Allocation Of ..." tables are imputation-rate metadata, not demographics.** ~70 table numbers (`B99011`…`B992522`) report how many respondents had a given field *imputed* rather than reported — useful for data-quality questions, actively misleading if surfaced as if it answers "how many people have X."

**Vintage mixing.** 2019 and 2020 CBG boundaries differ (220,333 vs 242,335 total CBGs) because the 2020 decennial redrew block groups. Never join a `2019_*` CBG row to a `2020_*` CBG row expecting them to represent the same physical area — always pick one vintage per query and stay in it.

---

## Appendix A — FTS-viability probe (architecture §12)

**Decision rule** (architecture §12): 5 obscure natural-language variable lookups; ≥4/5 resolve → FTS stands; else embeddings enter, scoped to variable search only.

**Verdict: FTS stands. Embeddings are not needed.** Of the probes whose target variable actually exists in the dataset, tokenized matching resolved **7/7**; naive substring `LIKE` resolved 6/7. The two apparent early failures were *dataset coverage gaps*, not retrieval failures — no retrieval method, lexical or semantic, can return a variable the bundle doesn't contain.

All probes run against `2019_METADATA_CBG_FIELD_DESCRIPTIONS` via `scripts/sf_query.py`, searching the concatenated `TABLE_TITLE` + all ten `FIELD_LEVEL_*` breadcrumb columns.

| # | Natural-language query | Target variable | Substring `LIKE` | Token-AND (FTS-like) |
|---|---|---|---|---|
| 1 | people who walk to work | `B08301e19` / `B08134` "Walked" | hit | hit |
| 2 | grandparents raising grandchildren | **absent from dataset** | false positive (`B99102`) | correctly empty |
| 3 | households with no vehicle available | `B25044` "No vehicle available" | hit | hit |
| 4 | people without health insurance | `B27010` | hit | hit |
| 5 | unmarried couples living together | **absent from dataset** | wrong table (`B09018`) | correctly empty |
| 6 | people who work from home | `B08301e21` "Worked from home" | **miss** | hit |
| 7 | homes without indoor plumbing | `B25016` "Lacking complete plumbing facilities" | hit | hit |
| 8 | how much is rent | `B25063` "Gross Rent" | hit | hit |
| 9 | kids living in poverty | `B17010` | hit | hit |

### Three findings that constrain the snapshot builder

**1. Substring matching is not sufficient; tokenized matching + ranking is required.** Probe 6 searched `"WORKED AT HOME"`; the Census label reads `"Worked from home"`. A one-preposition difference produced zero rows under `LIKE`. Under token-AND (`WORK` ∧ `HOME`) the target `B08301e21` *is* recalled — verified directly, count = 1 — but arrives buried behind ~100 `B08134` rows because `LIKE` has no relevance ordering. **Recall is not the problem; ranking is.** This is precisely what FTS5 + BM25 supplies, and it is the empirical justification for the architecture's FTS choice over embeddings: the vocabulary gap here is morphological/functional (prepositions, inflection), not semantic.

**2. `B99*` allocation tables must be excluded from the search corpus.** 67 of 364 distinct `TABLE_NUMBER`s are `B99*`/`B992*` "Allocation Of …" imputation-rate tables (297 are real demographic tables). Probe 2 surfaced `B99102` "Allocation Of Grandparents Living With Grandchildren" as its top result — an imputation-rate table masquerading as a demographic answer. Left in the corpus, these actively generate wrong answers to plausible questions.

**3. Snowflake's `CONCAT_WS` returns `NULL` if *any* argument is `NULL`.** Unlike Postgres/MySQL, which skip nulls. Since breadcrumb depth varies per row (most rows have `NULL` beyond `FIELD_LEVEL_6`), the first probe run returned 0 rows for *all five* queries — a silent, total, false-negative wipeout that looked like a real result. Every `FIELD_LEVEL_*` column must be `COALESCE(col, '')`-wrapped when assembling searchable text. Verified: `SELECT CONCAT_WS(' ', 'A', NULL, 'B')` → `NULL`.

### Coverage finding — variables genuinely absent

The SafeGraph bundle carries a **subset** of the ACS catalog: 297 real demographic table numbers across 28 topic groups (`B01,B02,B03,B07,B08,B09,B11,B12,B14,B15,B16,B17,B19,B20,B21,B22,B23,B24,B25,B27,B28,B29` + `C02,C15,C16,C17,C21,C24`). Confirmed absences include:

- **Grandparents raising grandchildren** — only `B99102/B99103/B99104` allocation tables exist; the real `B10050`/`B10051` tables are not in the bundle.
- **Unmarried-partner households** — zero tables match `UNMARRIED` or `PARTNER` in any title (`B11009` absent).

Confirmed *present* despite the gaps: citizenship/nativity (48 fields) and veteran status (184 fields).

These absences are a product asset, not just a limitation: they are the natural source material for the `partial_match` and `unanswerable` golden-eval categories, and the correct agent behavior is an honest "this dataset doesn't carry that variable" rather than substituting a near-miss like the `B99*` allocation table.
