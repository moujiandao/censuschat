"""Tests for the SQL gate — the trust boundary between the agent and Snowflake.

This file is the executable spec for `validate_sql` (GitHub issue #1). Every
exit criterion in that issue maps to at least one test here. Written first
per CLAUDE.md rule 19.

Two rules to keep in mind when reading:
  * default deny — anything not positively recognised as a bounded, read-only,
    allowlisted SELECT must fail;
  * `validate_sql` never raises. A rejection is a returned `SqlGateResult`,
    because the agent gets a bounded rewrite opportunity from it (rule 9).
"""

from __future__ import annotations

import pytest
import sqlglot

from src.contracts import (
    _DEMOGRAPHIC_TABLES,
    _METADATA_TABLES,
    ALLOWED_TABLES,
    SQL_ROW_LIMIT,
    SqlGateResult,
    SqlViolation,
)
from src.sqlgate import validate_sql

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

B01 = 'US_CENSUS.PUBLIC."2020_CBG_B01"'
B02 = 'US_CENSUS.PUBLIC."2020_CBG_B02"'
FIPS = 'US_CENSUS.PUBLIC."2020_METADATA_CBG_FIPS_CODES"'


def qualify(allowlist_name: str) -> str:
    """`US_CENSUS.PUBLIC.2020_CBG_B01` -> `US_CENSUS.PUBLIC."2020_CBG_B01"`.

    ALLOWED_TABLES stores quote-stripped names; the physical identifiers need
    double quotes in SQL because they start with a digit.
    """
    catalog, db, table = allowlist_name.split(".")
    return f'{catalog}.{db}."{table}"'


def rejected_2019_tables() -> list[str]:
    """Every 2019-vintage name the loader ships (decision D-003)."""
    return (
        [f"US_CENSUS.PUBLIC.2019_CBG_{t}" for t in _DEMOGRAPHIC_TABLES]
        + [f"US_CENSUS.PUBLIC.2019_{t}" for t in _METADATA_TABLES]
        + ["US_CENSUS.PUBLIC.2019_CBG_B99", "US_CENSUS.PUBLIC.2019_CBG_PATTERNS"]
    )


def row_bound(sql: str) -> int:
    """The top-level row bound of a statement, read back from the AST rather
    than matched in the string. `LIMIT n` and `FETCH FIRST n ROWS` occupy the
    same slot but spell the count differently."""
    node = sqlglot.parse_one(sql, dialect="snowflake").args["limit"]
    count = node.args.get("expression") or node.args.get("count")
    return int(count.this)


def assert_rejected(result: SqlGateResult, violation: SqlViolation) -> None:
    assert result.ok is False
    assert violation in result.violations
    assert result.detail, "a rejection must explain itself — the agent rewrites from it"


# --------------------------------------------------------------------------
# Parse errors
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELEC 1 FROM",
        "SELECT FROM WHERE",
        f"SELECT a FROM {B01} WHERE (",
        "",
        "   ",
        "-- just a comment",
    ],
)
def test_unparseable_sql_is_a_parse_error(sql: str) -> None:
    assert_rejected(validate_sql(sql), SqlViolation.PARSE_ERROR)


def test_validate_sql_never_raises() -> None:
    """A malformed string is a returned rejection, never an exception — the
    agent's recovery loop depends on getting a result back."""
    for sql in ("", ")))", "SELECT", "\x00", "DROP", "SELECT 1; SELECT ("):
        assert isinstance(validate_sql(sql), SqlGateResult)


# --------------------------------------------------------------------------
# Multi-statement
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2;",
        f"SELECT a FROM {B01}; SELECT b FROM {B02}",
        f"SELECT a FROM {B01}; DROP TABLE {B01}",
    ],
)
def test_multi_statement_is_rejected(sql: str) -> None:
    assert_rejected(validate_sql(sql), SqlViolation.MULTI_STATEMENT)


def test_trailing_semicolon_on_a_single_statement_is_fine() -> None:
    assert validate_sql(f"SELECT a FROM {B01};").ok is True


# --------------------------------------------------------------------------
# Not a SELECT — DML / DDL
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        f"INSERT INTO {B01} (a) VALUES (1)",
        f"UPDATE {B01} SET a = 1",
        f"DELETE FROM {B01}",
        f"TRUNCATE TABLE {B01}",
    ],
)
def test_dml_is_rejected(sql: str) -> None:
    assert_rejected(validate_sql(sql), SqlViolation.NOT_SELECT)


@pytest.mark.parametrize(
    "sql",
    [
        f"DROP TABLE {B01}",
        "CREATE TABLE z (a INT)",
        f"CREATE TABLE z AS SELECT a FROM {B01}",
        f"ALTER TABLE {B01} ADD COLUMN z INT",
        "CREATE VIEW v AS SELECT 1",
        "GRANT SELECT ON T TO ROLE R",
        "USE WAREHOUSE COMPUTE_WH",
        f"DESCRIBE TABLE {B01}",
        "SHOW TABLES",
        # sqlglot has no UNSET node and mis-parses it as an alias expression.
        # It is still not a query, which is the only thing the gate needs.
        "UNSET my_var",
        # Exfiltration to an external stage — a more severe write than the
        # INSERT/UPDATE cases above, and the one most worth pinning down.
        f"COPY INTO 's3://bucket/out' FROM (SELECT a FROM {B01})",
    ],
)
def test_ddl_and_non_query_statements_are_rejected(sql: str) -> None:
    assert_rejected(validate_sql(sql), SqlViolation.NOT_SELECT)


# --------------------------------------------------------------------------
# Table allowlist
# --------------------------------------------------------------------------

@pytest.mark.parametrize("table", sorted(ALLOWED_TABLES))
def test_every_allowed_table_passes(table: str) -> None:
    """Sweep the whole allowlist — a typo in one entry should surface here,
    not at request time against Snowflake."""
    result = validate_sql(f"SELECT a FROM {qualify(table)}")
    assert result.ok is True, result.detail
    assert result.violations == []


@pytest.mark.parametrize("table", sorted(rejected_2019_tables()))
def test_every_2019_table_is_rejected(table: str) -> None:
    """D-003: block groups were redrawn for 2020 and the 5-year windows
    overlap, so cross-vintage comparison is invalid. Enforced here, not in a
    prompt."""
    assert_rejected(validate_sql(f"SELECT a FROM {qualify(table)}"), SqlViolation.TABLE_NOT_ALLOWED)


@pytest.mark.parametrize(
    "table",
    [
        "US_CENSUS.PUBLIC.2020_CBG_B99",
        "US_CENSUS.PUBLIC.2020_CBG_GEOMETRY_WKT",
        "US_CENSUS.PUBLIC.2020_REDISTRICTING_CBG_DATA",  # D-004, not until M3
        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY",
        "US_CENSUS.INFORMATION_SCHEMA.TABLES",
    ],
)
def test_deliberately_excluded_tables_are_rejected(table: str) -> None:
    assert_rejected(validate_sql(f"SELECT a FROM {qualify(table)}"), SqlViolation.TABLE_NOT_ALLOWED)


def test_unqualified_table_is_rejected() -> None:
    """Default deny: the allowlist holds three-part names, so a bare table
    name can never match one."""
    assert_rejected(validate_sql('SELECT a FROM "2020_CBG_B01"'), SqlViolation.TABLE_NOT_ALLOWED)


def test_unquoted_catalog_and_schema_match_case_insensitively() -> None:
    """Snowflake folds unquoted identifiers to upper case, so this names the
    same physical table."""
    assert validate_sql('SELECT a FROM us_census.public."2020_CBG_B01"').ok is True


def test_lowercase_quoted_table_name_is_rejected() -> None:
    """A quoted identifier is case-SENSITIVE in Snowflake, so
    `"2020_cbg_b01"` is a different (non-existent) table. Rejecting it here
    turns a confusing runtime error into a rewrite the agent can act on."""
    assert_rejected(
        validate_sql('SELECT a FROM US_CENSUS.PUBLIC."2020_cbg_b01"'),
        SqlViolation.TABLE_NOT_ALLOWED,
    )


def test_fully_quoted_three_part_name_passes() -> None:
    assert validate_sql('SELECT a FROM "US_CENSUS"."PUBLIC"."2020_CBG_B01"').ok is True


def test_join_across_two_allowed_tables_passes() -> None:
    sql = (
        f"SELECT t.a, u.b FROM {B01} t "
        f"JOIN {FIPS} u ON t.CENSUS_BLOCK_GROUP = u.CENSUS_BLOCK_GROUP"
    )
    assert validate_sql(sql).ok is True


def test_one_disallowed_table_in_a_join_rejects_the_whole_statement() -> None:
    sql = f'SELECT t.a FROM {B01} t JOIN US_CENSUS.PUBLIC."2019_CBG_B01" u ON t.x = u.x'
    result = validate_sql(sql)
    assert_rejected(result, SqlViolation.TABLE_NOT_ALLOWED)
    assert "2019_CBG_B01" in result.detail


def test_disallowed_table_nested_in_a_subquery_is_found() -> None:
    """The allowlist walks the whole tree, not just the top-level FROM."""
    sql = f'SELECT a FROM (SELECT a FROM US_CENSUS.PUBLIC."2019_CBG_B01")'
    assert_rejected(validate_sql(sql), SqlViolation.TABLE_NOT_ALLOWED)


def test_union_of_allowed_tables_passes() -> None:
    assert validate_sql(f"SELECT a FROM {B01} UNION ALL SELECT a FROM {B02}").ok is True


def test_union_branch_with_disallowed_table_is_rejected() -> None:
    sql = f'SELECT a FROM {B01} UNION ALL SELECT a FROM US_CENSUS.PUBLIC."2019_CBG_B01"'
    assert_rejected(validate_sql(sql), SqlViolation.TABLE_NOT_ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT SYSTEM$CANCEL_ALL_QUERIES()",
        "SELECT CURRENT_USER()",
        "SELECT a FROM TABLE(RESULT_SCAN('01b2'))",
    ],
)
def test_statement_referencing_no_allowed_table_is_rejected(sql: str) -> None:
    """Zero table references is not "no violation" — it is the shape every
    table-function and metadata-probe escape takes, since a table function
    produces no Table node to check. A census answer always reads a census
    table."""
    assert_rejected(validate_sql(sql), SqlViolation.TABLE_NOT_ALLOWED)


def test_allowed_tables_parameter_is_honored() -> None:
    """The allowlist is an argument, not a global read — tests and future
    callers must be able to narrow it."""
    narrow = frozenset({"US_CENSUS.PUBLIC.2020_CBG_B01"})
    assert validate_sql(f"SELECT a FROM {B01}", allowed_tables=narrow).ok is True
    assert_rejected(
        validate_sql(f"SELECT a FROM {B02}", allowed_tables=narrow),
        SqlViolation.TABLE_NOT_ALLOWED,
    )


# --------------------------------------------------------------------------
# LIMIT injection
# --------------------------------------------------------------------------

def test_limit_is_injected_when_absent() -> None:
    result = validate_sql(f"SELECT a FROM {B01}")
    assert result.ok is True
    assert f"LIMIT {SQL_ROW_LIMIT}" in result.sql.upper()


def test_injected_limit_survives_a_reparse() -> None:
    """Assert on the parsed value, not the string — proves the LIMIT is real
    syntax and not text glued onto a comment or a trailing clause."""
    result = validate_sql(f"SELECT a FROM {B01} ORDER BY a")
    assert row_bound(result.sql) == SQL_ROW_LIMIT


def test_limit_is_injected_on_a_union() -> None:
    result = validate_sql(f"SELECT a FROM {B01} UNION ALL SELECT a FROM {B02}")
    assert result.ok is True
    assert f"LIMIT {SQL_ROW_LIMIT}" in result.sql.upper()


def test_limit_is_injected_on_a_bare_parenthesized_query() -> None:
    """`(SELECT ...)` with no CTE/UNION is a parse-tree shape distinct from
    a plain SELECT. Confirmed against real Snowflake (not just sqlglot's
    printer) that `(SELECT ...) LIMIT N` is valid syntax and the LIMIT
    genuinely bounds the row count, not just cosmetically present in the
    string: `python scripts/sf_query.py '(SELECT "B01001e1" FROM
    US_CENSUS.PUBLIC."2020_CBG_B01") LIMIT 200'` returned exactly 200 rows."""
    result = validate_sql(f"(SELECT a FROM {B01})")
    assert result.ok is True
    assert row_bound(result.sql) == SQL_ROW_LIMIT


def test_existing_smaller_limit_is_preserved_unmodified() -> None:
    result = validate_sql(f"SELECT a FROM {B01} LIMIT 5")
    assert result.ok is True
    assert row_bound(result.sql) == 5


def test_existing_limit_equal_to_the_cap_is_preserved() -> None:
    result = validate_sql(f"SELECT a FROM {B01} LIMIT {SQL_ROW_LIMIT}")
    assert row_bound(result.sql) == SQL_ROW_LIMIT


def test_existing_limit_above_the_cap_is_clamped_down() -> None:
    """SQL_ROW_LIMIT is a bound, not a default. A gate that honors
    `LIMIT 100000` bounds nothing — the model could lift its own ceiling by
    asking for it, which is exactly the token blowout D-007 reasons about."""
    result = validate_sql(f"SELECT a FROM {B01} LIMIT 100000")
    assert result.ok is True
    assert row_bound(result.sql) == SQL_ROW_LIMIT


def test_fetch_first_n_rows_counts_as_an_existing_bound() -> None:
    """Snowflake's FETCH is a row bound too; sqlglot normalises it into the
    same `limit` slot, so the gate must not double-bound it."""
    result = validate_sql(f"SELECT a FROM {B01} FETCH FIRST 5 ROWS ONLY")
    assert result.ok is True
    assert row_bound(result.sql) == 5


def test_inner_limit_does_not_satisfy_the_outer_bound() -> None:
    """A LIMIT inside a subquery bounds the subquery, not the result set."""
    result = validate_sql(f"SELECT a FROM (SELECT a FROM {B01} LIMIT 5) x")
    assert result.ok is True
    assert row_bound(result.sql) == SQL_ROW_LIMIT


# --------------------------------------------------------------------------
# CTEs
# --------------------------------------------------------------------------

def test_cte_resolving_to_a_named_column_select_passes() -> None:
    sql = (
        f"WITH state_pop AS ("
        f"  SELECT SUBSTR(CENSUS_BLOCK_GROUP, 1, 2) AS st, SUM(B01001e1) AS pop"
        f"  FROM {B01} GROUP BY 1"
        f") SELECT st, pop FROM state_pop ORDER BY pop DESC"
    )
    result = validate_sql(sql)
    assert result.ok is True, result.detail
    assert f"LIMIT {SQL_ROW_LIMIT}" in result.sql.upper()


def test_cte_name_is_not_treated_as_a_table_reference() -> None:
    """`FROM state_pop` must not be checked against the allowlist."""
    sql = f"WITH state_pop AS (SELECT a FROM {B01}) SELECT a FROM state_pop"
    result = validate_sql(sql)
    assert result.ok is True
    assert SqlViolation.TABLE_NOT_ALLOWED not in result.violations


def test_cte_over_a_disallowed_table_is_rejected() -> None:
    sql = f'WITH x AS (SELECT a FROM US_CENSUS.PUBLIC."2019_CBG_B01") SELECT a FROM x'
    assert_rejected(validate_sql(sql), SqlViolation.TABLE_NOT_ALLOWED)


def test_a_cte_does_not_shadow_a_table_name_outside_its_own_scope() -> None:
    """A CTE only shadows a bare table name where it is lexically in scope.

    The bypass this guards: define a decoy CTE named after a forbidden table
    deep inside one branch, then reference that bare name from a *different*
    branch where the CTE is not in scope. A gate that collects CTE names into
    one flat set excuses both references; Snowflake resolves the second one
    against the real 2019 table, defeating D-003.
    """
    sql = (
        f'SELECT a FROM {B01} '
        f'WHERE CENSUS_BLOCK_GROUP IN (SELECT CENSUS_BLOCK_GROUP FROM "2019_CBG_B01") '
        f'UNION ALL '
        f'SELECT x AS a FROM (WITH "2019_CBG_B01" AS (SELECT 1 AS x) SELECT x FROM "2019_CBG_B01") AS decoy'
    )
    assert_rejected(validate_sql(sql), SqlViolation.TABLE_NOT_ALLOWED)


def test_a_cte_shadowing_a_forbidden_name_in_its_own_scope_is_still_read_as_a_cte() -> None:
    """The flip side: where the CTE really is in scope, Snowflake resolves the
    name to the CTE, so no forbidden table is read. The statement then fails
    only because it reads no allowed table at all."""
    sql = f'WITH "2019_CBG_B01" AS (SELECT 1 AS x) SELECT x FROM "2019_CBG_B01"'
    result = validate_sql(sql)
    assert result.ok is False
    assert "does not read any allowed table" in result.detail


def test_a_forward_reference_to_a_later_cte_is_not_treated_as_a_cte() -> None:
    """Within one WITH list, only CTEs declared earlier are in scope.

    The second half of the scoping bypass: declare the decoy CTE *after* the
    one that references its name. Snowflake resolves that forward reference
    against the physical table, so treating it as a CTE reference lets the
    2019 data through. Scope is declaration order, not membership.
    """
    sql = (
        f'WITH leak AS (SELECT cbg FROM "2019_CBG_B01"), '
        f'     "2019_CBG_B01" AS (SELECT 1 AS cbg) '
        f'SELECT a.CENSUS_BLOCK_GROUP FROM {B01} AS a '
        f'WHERE a.CENSUS_BLOCK_GROUP IN (SELECT cbg FROM leak)'
    )
    assert_rejected(validate_sql(sql), SqlViolation.TABLE_NOT_ALLOWED)


def test_a_cte_may_reference_an_earlier_cte() -> None:
    """The legitimate direction still works — this is the ordinary way a
    multi-step aggregation gets written."""
    sql = (
        f"WITH base AS (SELECT SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS county, B01001e1 AS pop FROM {B01}), "
        f"rolled AS (SELECT county, SUM(pop) AS pop FROM base GROUP BY county) "
        f"SELECT county, pop FROM rolled"
    )
    result = validate_sql(sql)
    assert result.ok is True, result.detail


def test_multiple_ctes_pass() -> None:
    sql = (
        f"WITH x AS (SELECT a FROM {B01}), y AS (SELECT b FROM {B02}) "
        f"SELECT x.a, y.b FROM x JOIN y ON x.a = y.b"
    )
    assert validate_sql(sql).ok is True


# --------------------------------------------------------------------------
# Star projection (D-007)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM {B01}",
        f"SELECT t.* FROM {B01} t",
        f"SELECT a, t.* FROM {B01} t",
        f"SELECT * EXCLUDE (a) FROM {B01}",
        f"SELECT a FROM (SELECT * FROM {B01}) x",
        f"WITH x AS (SELECT * FROM {B01}) SELECT a FROM x",
        f"WITH x AS (SELECT a FROM {B01}) SELECT * FROM x",
        f"SELECT * FROM {B01} UNION ALL SELECT * FROM {B02}",
        f"SELECT (SELECT * FROM {B01}) FROM {B02}",
        # Star laundered through a function. OBJECT_CONSTRUCT(*) reads every
        # column and returns it as one JSON blob, so it costs exactly what
        # SELECT * costs while looking like a single projected column.
        f"SELECT OBJECT_CONSTRUCT(*) FROM {B01}",
        f"SELECT ARRAY_CONSTRUCT(*) FROM {B01}",
        f"SELECT HASH(*) FROM {B01}",
    ],
)
def test_star_projection_is_rejected_anywhere_in_the_tree(sql: str) -> None:
    """D-007: SQL_ROW_LIMIT bounds tokens, the projection rule bounds scan
    cost. The B/C tables average ~280 columns, so `SELECT *` under
    `LIMIT 200` still returns ~56,000 cells while passing every other check.
    The check walks every SELECT node, including CTE bodies and subqueries."""
    assert_rejected(validate_sql(sql), SqlViolation.BANNED_CONSTRUCT)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT COUNT(*) FROM {B01}",
        f"SELECT COUNT(*) AS n, SUM(B01001e1) FROM {B01}",
        f"WITH x AS (SELECT COUNT(*) AS n FROM {B01}) SELECT n FROM x",
        f"SELECT COUNT(*) OVER (PARTITION BY a) AS n FROM {B01}",
        f"SELECT a FROM {B01} GROUP BY a HAVING COUNT(*) > 1",
    ],
)
def test_count_star_is_not_a_star_projection(sql: str) -> None:
    """COUNT(*) is the one star that reads no columns, so D-007's scan-cost
    argument does not apply to it. This is the guard against the star rule
    being written too broadly — it is the only exemption."""
    result = validate_sql(sql)
    assert result.ok is True, result.detail


# --------------------------------------------------------------------------
# Other banned constructs
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT a INTO scratch FROM {B01}",
        f"SELECT a INTO TABLE scratch FROM {B01}",
    ],
)
def test_into_is_rejected(sql: str) -> None:
    """`SELECT ... INTO` is a write wearing a SELECT's clothes — sqlglot even
    renders it back as CREATE TABLE AS."""
    assert_rejected(validate_sql(sql), SqlViolation.BANNED_CONSTRUCT)


@pytest.mark.parametrize(
    "sql",
    [
        "CALL my_procedure()",
        "CALL SYSTEM$WAIT(10)",
        "EXECUTE IMMEDIATE 'SELECT 1'",
        f"EXPLAIN SELECT a FROM {B01}",
    ],
)
def test_procedure_calls_and_raw_commands_are_rejected(sql: str) -> None:
    """Anything sqlglot can only represent as an opaque Command is, by
    definition, syntax the gate cannot reason about. Default deny."""
    assert_rejected(validate_sql(sql), SqlViolation.BANNED_CONSTRUCT)


@pytest.mark.parametrize(
    "sql",
    [
        "SET my_var = 1",
        f"SELECT $my_var FROM {B01}",
        f"SELECT a FROM {B01} WHERE b = $threshold",
        f"SELECT $1 FROM {B01}",
    ],
)
def test_session_variables_are_rejected(sql: str) -> None:
    assert_rejected(validate_sql(sql), SqlViolation.BANNED_CONSTRUCT)


@pytest.mark.parametrize(
    "sql",
    [
        # Side-effecting system functions: a write wearing a SELECT's clothes,
        # exactly what `SELECT ... INTO` is banned for.
        f"SELECT a, SYSTEM$CANCEL_ALL_QUERIES() FROM {B01}",
        f"SELECT a FROM {B01} WHERE 1 = SYSTEM$WAIT(60)",
        f"SELECT SYSTEM$GET_PREDECESSOR_RETURN_VALUE() FROM {B01}",
        # Functions that name an object with a string instead of a table
        # reference. The allowlist walks Table nodes, so these read whatever
        # the role can see without the allowlist ever running.
        f"""SELECT GET_DDL('TABLE', 'US_CENSUS.PUBLIC."2019_CBG_B99"'), a FROM {B01}""",
        f"""SELECT IDENTIFIER('US_CENSUS.PUBLIC."2019_CBG_B01"') FROM {B01}""",
        f"SELECT a FROM {B01} WHERE b IN (SELECT $1 FROM TABLE(RESULT_SCAN('01b2')))",
        f"SELECT LAST_QUERY_ID(), a FROM {B01}",
        f"SELECT GETVARIABLE('secret'), a FROM {B01}",
    ],
)
def test_object_reference_and_system_functions_are_rejected(sql: str) -> None:
    """An allowlisted FROM clause does not launder the rest of the statement.
    These all pass the table check because the object they touch is a string
    literal or a side effect, not a table reference."""
    assert_rejected(validate_sql(sql), SqlViolation.BANNED_CONSTRUCT)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT SUM(B01001e1) FROM {B01}",
        f"SELECT AVG(B01001e1), MIN(B01001e1), MAX(B01001e1) FROM {B01}",
        f"SELECT SUBSTR(CENSUS_BLOCK_GROUP, 1, 5) AS county FROM {B01}",
        f"SELECT ROUND(DIV0(SUM(a), SUM(b)), 2) FROM {B01}",
        f"SELECT COALESCE(CAST(a AS INT), 0) FROM {B01}",
        f"SELECT ROW_NUMBER() OVER (ORDER BY a) AS rn FROM {B01}",
        f"SELECT LISTAGG(a, ',') FROM {B01}",
        f"SELECT MEDIAN(a), TO_CHAR(b) FROM {B01}",
        f"SELECT IFF(a > 0, a, NULL) FROM {B01}",
    ],
)
def test_ordinary_census_functions_still_pass(sql: str) -> None:
    """Guard against the function rule being written as a blanket ban. These
    are the shapes real census answers are made of — aggregation, FIPS-prefix
    truncation, safe division — and rejecting one burns a bounded retry on a
    question the agent could have answered."""
    result = validate_sql(sql)
    assert result.ok is True, result.detail


# --------------------------------------------------------------------------
# Result shape
# --------------------------------------------------------------------------

def test_ok_result_returns_parseable_sanitized_sql() -> None:
    result = validate_sql(f"SELECT B01001e1 FROM {B01} WHERE CENSUS_BLOCK_GROUP = '011010201001'")
    assert result.ok is True
    assert result.violations == []
    assert result.detail is None
    sqlglot.parse_one(result.sql, dialect="snowflake")  # must round-trip


def test_rejection_never_returns_executable_sql() -> None:
    """A caller that ignores `ok` must not find a runnable statement in
    `.sql`. Belt and braces around the one field a bug could leak through."""
    result = validate_sql(f"DROP TABLE {B01}")
    assert result.ok is False
    assert result.sql == ""


def test_violations_are_deduplicated() -> None:
    sql = f"SELECT * FROM {B01} UNION ALL SELECT * FROM {B02}"
    result = validate_sql(sql)
    assert len(result.violations) == len(set(result.violations))


def test_star_and_bad_table_report_both_violations() -> None:
    """The agent rewrites from `detail`; surfacing one problem at a time
    burns its two bounded retries (rule 9)."""
    result = validate_sql('SELECT * FROM US_CENSUS.PUBLIC."2019_CBG_B01"')
    assert result.ok is False
    assert SqlViolation.BANNED_CONSTRUCT in result.violations
    assert SqlViolation.TABLE_NOT_ALLOWED in result.violations


def test_input_string_is_not_mutated() -> None:
    sql = f"SELECT a FROM {B01}"
    before = str(sql)
    validate_sql(sql)
    assert sql == before
