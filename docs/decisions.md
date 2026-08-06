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

## D-011 — Median-variable detection verified against live data (2026-08-05, resolved 2026-08-06)

**Status: resolved. Verified against live Snowflake during issue #7 — the
substring heuristic has 100% precision and recall against the real
28-table list.**

D-009/C-3 requires `VariableHit.geo_levels` to return `[GeoLevel.BLOCK_GROUP]`
for the 28 median-table variables and all five levels otherwise — the
mechanism that prevents a median from being silently (and wrongly) averaged
up to county/state. `src/tools.py:_geo_levels_for` implements this by
checking whether `"median"` (case-insensitive) appears in the variable's
`TABLE_TITLE`.

**Originally flagged as an unverified assumption** (issues #3/#4/#5 session
had no live Snowflake connection). Resolved once live access became
available (issue #7 session):

```sql
SELECT COUNT(DISTINCT TABLE_NUMBER)
FROM US_CENSUS.PUBLIC."2020_METADATA_CBG_FIELD_DESCRIPTIONS"
WHERE LOWER(TABLE_TITLE) LIKE '%median%'
-- 28, exact match to the D-009/PRD §3 count
```

Listing all 28 (`B01002*` median-age variants, `B19013`/`B19049`/`B19113`/
`B19202`/`B29004` median income variants, `B20002`/`B20017` median earnings,
`B25018`/`B25021`/`B25035`/`B25037`/`B25039`/`B25058`/`B25064`/`B25071`/
`B25077`/`B25083`/`B25088`/`B25092` median housing variants) confirms every
row's `TABLE_TITLE` literally starts with "Median" — zero false positives,
zero false negatives. The heuristic needs no change.

---

## D-012 — Two `2020_METADATA_CBG_FIPS_CODES` shape corrections found via live build (2026-08-06)

**Status:** refinement, not a deviation — no approval required. Found
building the real snapshot (`build_snapshot(force=True)` against live
Snowflake) while verifying issue #7's DF-01 golden scenario end-to-end.
Neither synthetic test fixture (issues #2, #3/4/5) encoded the real data
shape, so both were invisible to the full 224-test suite before this.

**1. `STATE` is the two-letter postal abbreviation, not the full name.**
`schema-notes.md`'s "state/county FIPS → names" description reads as "full
names." Verified: `SELECT DISTINCT STATE, STATE_FIPS ... WHERE STATE_FIPS IN
('06','17')` → `CA`/`06`, `IL`/`17`. `src/tools.py`'s original
`_STATE_POSTAL_ABBR` lookup (full name → abbreviation) had the transform
backwards — it was applied to a value that was already an abbreviation.
Fixed by adding `src/us_states.py` (bidirectional USPS name↔abbreviation
reference data, static and public, unrelated to the Snowflake recon):
`src/snapshot.py` now builds the display `name` field (`"Alameda County,
California"`) from the raw abbreviation at build time; `src/tools.py`
normalizes a caller's full-name or abbreviation input to the abbreviation
before matching the `state` column, which stores the abbreviation
unconverted (matching `GeoCandidate.state`'s documented contract, "postal
abbr, for disambiguation display").

**2. 13 county-grain rows have `STATE = NULL`.** `STATE_FIPS` 60/66/69/78 —
American Samoa, Guam, Northern Mariana Islands, U.S. Virgin Islands — carry
a `COUNTY` (county-equivalent) name but no `STATE` value; the column is
genuinely empty for territories, not a data-loader defect. `build_snapshot`
originally NOT-NULLed `geography.state` and crashed on the real data
(`sqlite3.IntegrityError`) the first time it ran against Snowflake instead
of a fixture. Fixed by excluding rows with `STATE IS NULL` from the
geography index — out of scope for this share's state+county lookup (every
golden scenario and `resolve_geography`'s own contract are 50-states-and-DC
only), and inventing a territory display-name policy with zero evidence
behind it would be worse than an honest exclusion.

**Cost accepted:** none material. Territories were never in scope for any
golden scenario; the abbreviation fix is a pure correctness fix with no
tradeoff — `resolve_geography("Alameda County, California")` returned zero
candidates before it, `06001` after.

---

## D-013 — Bounded recovery counts every `run_census_sql` failure, not just `SqlRejected` (2026-08-06)

**Status:** refinement of issue #12's exit-criteria wording, not a deviation
from CLAUDE.md — no approval required. CLAUDE.md rule 9 reads "after a SQL
error or zero-row result, at most 2 retries"; issue #12's exit criteria
narrow that to literally "a `SqlRejected` error or a zero-row `QueryResult`".
Implemented the narrow reading first, then found it wrong via live
verification against real Snowflake.

A genuine Snowflake execution error — e.g. an unquoted mixed-case column
identifier Snowflake folds to uppercase and can't resolve
(`B01003e1` unquoted → `B01003E1`, invalid) — is caught by `agent_turn`'s
generic `except Exception` branch, not `SqlRejected` (which only fires on
`validate_sql` gate rejections, before any Snowflake call). Under the narrow
reading this class of failure spent no recovery budget, so a model stuck
guessing bad SQL against real Snowflake could retry indefinitely at full
network/warehouse cost — the exact unbounded-cost outcome rule 9 exists to
prevent, and the SqlRejected-only reading is bounded by nothing on this
path.

**Fix:** any `is_error` outcome from `run_census_sql` (gate rejection or
execution error) now counts toward `MAX_RECOVERY_RETRIES`, alongside
zero-row results. Live-verified twice: a genuine repeated column-naming
error now stops at 2 attempts with an honest failure naming both errors
(previously ran a 3rd attempt); an unrelated unquoted-identifier failure
followed by a successful quoted retry still completes normally on retry 1,
confirming a real self-correction isn't penalized.

**Cost accepted:** none — this is strictly more conservative (tighter
bound) than the literal exit-criteria reading, and the only reading
consistent with rule 9's actual wording.

---

## D-014 — Ambiguous geography gets a code-enforced backstop, not prompt-only trust (2026-08-06)

**Status:** refinement of issue #13's exit criteria, not a deviation from
CLAUDE.md — no approval required. CLAUDE.md rule 10 and `GeoResolution`'s
own docstring both use MUST language ("the agent MUST ask, never silently
pick") for ambiguous geography. Issue #13's exit criterion 1 reads "Agent
loop checks `GeoResolution.ambiguous` ... and asks a clarifying question
instead of proceeding to SQL" — read literally, this asks for the *loop*
(code), not just the system prompt, to check the flag.

The rest of issue #13 (vintage-default framing, D-005's city/place redirect)
is implemented as system-prompt guidance only, verified live rather than by
unit test, per the issue's own Tests section ("agent-loop/LLM behavior...
covered by golden evals, not mocked unit asserts") and CLAUDE.md rule 19's
LLM-behavior exemption. Ambiguous-geography handling gets one exception:
`GeoResolution.ambiguous` is itself a deterministic boolean already computed
in code (`resolve_geography`, issue #4, TDD-covered), so unlike vintage
framing or city-redirect judgment, there's a concrete flag to check — the
same situation the SQL gate is in relative to the guardrail classifier
(CLAUDE.md rule 5: soft prompt layer vs. hard code boundary), and the same
lesson D-013 relearned empirically for bounded recovery: trusting the model
alone on a MUST-level invariant is not a boundary.

**Fix:** `src/agent.py:agent_turn` tracks `unresolved_ambiguous_geo`, a list
accumulated across the whole turn; if `run_census_sql` is attempted while
that list is non-empty, Snowflake is never called — the turn is
force-terminated with a deterministic, code-generated clarifying question
covering every unresolved candidate set (`_build_ambiguous_geo_clarification`),
the same code-enforced-termination pattern `_build_recovery_failure_message`
(D-013) already established. Scoped to `run_census_sql` only, matching the
exit criterion's literal wording — an unrelated, *unambiguous*
`resolve_geography` or `search_census_variables` call elsewhere in the turn
has no effect on this state; an unrelated *ambiguous* one is added to the
list alongside the first, and both must be resolved before `run_census_sql`
may proceed. 3 new TDD tests (`tests/test_agent.py`) plus live verification
of all four named golden scenarios (AMB-01 Washington County, AMB-02
Franklin County, AMB-03 Orange County, PM-03 Austin→Travis redirect) against
the real agent: in every live run the model asked correctly on its own
before the backstop was even needed, which is the expected steady-state —
the backstop is a safety net for the case it doesn't, and only the unit
tests exercise that path directly (no live run was engineered to trigger
it, since doing so would require adversarially prompting a well-behaved
model).

**Correction (code review, same day):** the first implementation tracked
pending ambiguity in a single last-write-wins variable
(`pending_ambiguous_geo: dict | None`), overwritten on *every*
`resolve_geography` call regardless of query. An unrelated, unambiguous
result for a second place later in the same turn silently cleared an
earlier still-unresolved ambiguity, so a subsequent `run_census_sql` for
the first (still-ambiguous) place passed the block check unblocked — a real
silent-pick path, exactly what exit criterion 2 forbids, and realistically
triggerable by any comparison question naming one of this project's own
name-collision counties (Washington/Franklin/Orange, D-002) alongside an
unambiguous one. The review also flagged the missing test for this exact
shape. Fixed by switching to the append-only list described above (`Fix`,
already updated to reflect the corrected behavior) and adding 2 more tests:
one with both `resolve_geography` calls in the same model response, one
with them split across separate tool-loop iterations — both confirm the
ambiguity is still caught. Full suite (255 tests) re-verified green after
the fix.

**Cost accepted:** none — strictly more conservative than prompt-only trust,
and the interpretation is at least as consistent with rule 10's MUST wording
as the pure-prompt reading. Coarse by construction in the direction that
matters for safety: once any ambiguous `resolve_geography` result appears
in a turn, *any* later `run_census_sql` call in that turn is blocked, even
one unrelated to that specific ambiguous place — avoiding a fragile "is
this SQL about that geography" check. Live runs above never exercised this
coarseness since the model resolved ambiguity before attempting SQL.

---

## D-015 — Snowflake reachability checked once at startup, not live per-request (2026-08-06)

**Status:** genuine deviation risk, resolved by Brian's explicit approval —
not a self-classified refinement like D-013/D-014.

Issue #15 (degraded mode + `/api/health`) requires knowing whether
Snowflake is currently reachable. The only way to know that for certain is
a live connection attempt. CLAUDE.md rule 13 reads, without qualification:
"At request time, Snowflake is touched solely by `run_census_sql`." A
straightforward implementation of issue #15's exit criteria — live-probing
Snowflake from both `/api/health` and `agent_turn`'s per-turn degraded
check — does exactly what rule 13 forbids, on every `/api/health` call and
on every chat turn whenever the local snapshot happens to be missing.

Code review (issue #15's first pass) flagged this correctly as BLOCKING:
no `docs/decisions.md` entry recorded the deviation, and the top-level
CLAUDE.md requires an entry plus Brian's explicit approval *before*
deviating from a numbered rule — not documentation after the fact, which
is how D-013/D-014 handled their own judgment calls (both self-classified
as refinements of ambiguous exit-criteria wording, not deviations from an
unambiguous rule). This case is different in kind: rule 13's text is not
ambiguous, so this could not be resolved the same way.

Two compliant-vs-deviating designs were presented:
- **Keep live per-request probing**, log it as an approved rule 13
  deviation. Health status stays continuously accurate, at the cost of a
  standing exception to the rule on every request.
- **Check once at startup, cache the result.** `/api/health` and
  `agent_turn` read the cached value only — Snowflake is never touched at
  request time for this purpose, so rule 13 stays intact with no
  deviation at all. Cost: the cached value can go stale if Snowflake
  changes state mid-run without an app restart.

**Decision (Brian, 2026-08-06):** the cached-at-startup design. Reasoning
recorded at the time: `/api/health` has no continuous production consumer
— `deploy.sh` only polls it for up to 60s right after a fresh deploy, and
there is no `docker-compose` healthcheck stanza — so the staleness
downside costs nothing today; nothing is watching that would catch a
mid-run change either way. And a genuine mid-session Snowflake outage is
already surfaced honestly the moment it matters: the next `run_census_sql`
call fails and bounded recovery (issue #12, `MAX_RECOVERY_RETRIES`) gives
an honest failure — that path is independent of `/api/health` and doesn't
need a fresh reachability probe to work correctly.

**Fix:** `src/health.py:check_snowflake_reachability()` is the only live
Snowflake probe in the module, called exactly once — from
`src/app.py`'s `lifespan` handler, alongside the existing `build_snapshot()`
call — and cached in a module-level variable. `is_degraded()` (the chat
hot path) and `health_report()` (`/api/health`) both read the cached value
only; tests assert the underlying connect function is never called from
either. Rule 13 is fully intact: Snowflake is touched at request time
solely by `run_census_sql`, exactly as written — no deviation to log.

**Related fix, same review round:** the connect call itself
(`src/snowflake_conn.py:connect`) had no timeout, so even the once-at-
startup probe (and `build_snapshot`'s own connection, and every
`run_census_sql` call) could hang indefinitely on a slow-but-not-erroring
Snowflake. Added `SNOWFLAKE_CONNECT_TIMEOUT_S=10` (`src/contracts.py`),
applied unconditionally as `login_timeout` — bounds only the login/auth
phase, so it can never interact with `SQL_STATEMENT_TIMEOUT_S` (which only
governs query execution after a session already exists). Live-verified: a
real `run_census_sql` query against an allowlisted table still succeeds
with the timeout in place.

**Cost accepted:** `/api/health` and the chat degraded-check can report
stale Snowflake status between app restarts. Judged acceptable per Brian's
reasoning above; revisit if `/api/health` ever gains a continuous
production consumer (e.g. a `docker-compose` healthcheck or external
uptime monitor), at which point a periodic background refresh would be
worth adding.

---

## D-016 — Native Caddy on the deploy host, not Compose's (2026-08-06)

**Status:** deviation from CLAUDE.md rule 18. **Approved by Brian during
the live deploy**, after the constraint was discovered on the host.

Rule 18 reads: "Deploy = Docker Compose (app + Caddy) on EC2 at
`https://censuschat.brianmar.com` behind basic auth. Caddy reaches the app
by compose service name, never `localhost`." The rule's intent is sound —
addressing a container by service name avoids depending on host port
layout, and keeps the whole stack reproducible from one compose file.

The host contradicts it. `18.191.3.58` already runs Caddy as a **native
systemd service**, owning ports 80 and 443, and that same Caddy also
serves `memory.brianmar.com` → `localhost:3010` (an unrelated
long-running container). Starting the bundled `caddy` service would fail
on a port conflict; stopping the native one to free the ports would take
`memory.brianmar.com` offline. Neither is acceptable for a rule whose
purpose is deployment hygiene, not uptime sacrifice.

Its `censuschat.brianmar.com` block was also *already* correct —
`basic_auth` plus `reverse_proxy 127.0.0.1:8000` with `flush_interval -1`,
which is exactly the setting SSE streaming needs (issue #10). The config
predated this work and was simply pointing at a port nothing listened on,
which is why the domain returned 401-with-no-app for so long.

**Fix:** `deploy.sh` starts only the `app` service
(`docker compose up -d --build app`), and a committed
`docker-compose.override.yml` publishes it on `127.0.0.1:8000`. The
bundled `caddy` service stays in `docker-compose.yml` unused, so a host
*without* a native Caddy could still bring up the full stack as rule 18
describes — the deviation is host-specific, not baked into the image.

**Cost accepted:** the deployed topology is no longer fully described by
`docker-compose.yml` alone; reproducing it requires the host's
`/etc/caddy/Caddyfile` too, which lives outside this repo. Documented in
README §Deploying. Binding to `127.0.0.1` rather than `0.0.0.0` preserves
the security property rule 18 implicitly protected: the app is
unreachable except through Caddy, so basic auth cannot be bypassed by
hitting port 8000 directly.

**Also found during this deploy, and fixed separately:** the `Dockerfile`
copied only `src/`, so `static/` and `evals/` were absent from the image —
`GET /` would have 500'd and the Evals tab shown nothing, i.e. the entire
web UI broken on the host while working perfectly on localhost. Caught by
reading the Dockerfile before deploying, not by any test, and verified by
building and running the real image (commit `ffe90ea`). A concrete
instance of the assignment's own warning that "your local machine doesn't
count."
