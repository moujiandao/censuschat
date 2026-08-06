"""SQL gate — the trust boundary between the agent and Snowflake.

Every statement that reaches a warehouse passes through `validate_sql` first
(CLAUDE.md rules 1 and 5). The classifier and the system prompt are soft
layers that a sufficiently creative input can talk past; this module is the
one that cannot be talked past, because it is code and it defaults to deny.

Pure function: no I/O, no network, no clock, no globals beyond the frozen
constants in `contracts`. That is what makes it exhaustively testable, and
`tests/test_sqlgate.py` is its executable spec.

What it enforces, in order:

  1. parses cleanly under sqlglot's Snowflake dialect
  2. exactly one statement
  3. no opaque command syntax (CALL, EXECUTE, EXPLAIN, SET)
  4. the statement is a query (SELECT / set operation / parenthesised query),
     CTEs allowed
  5. no banned construct anywhere in the tree: INTO, session variables,
     star projection (D-007)
  6. at least one table reference, and every one of them allowlisted
  7. bounded by LIMIT SQL_ROW_LIMIT

Rejections are returned, never raised — `run_census_sql` turns the result
into a tool error the agent gets a bounded rewrite from (rule 9).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from src.contracts import (
    ALLOWED_TABLES,
    SQL_ROW_LIMIT,
    SqlGateResult,
    SqlViolation,
)

# Statement types that are legitimate read-only queries. Everything else —
# every DML, DDL, GRANT, USE, SHOW, DESCRIBE and parse artifact — is NOT_SELECT
# by omission. Listing what is allowed rather than what is forbidden is the
# whole point: a new sqlglot node type lands on the deny side by default.
_QUERY_TYPES = (exp.Select, exp.SetOperation, exp.Subquery)

# sqlglot falls back to an opaque `Command` node for syntax it cannot model
# (CALL, EXECUTE IMMEDIATE, EXPLAIN, bare DROP). An expression the gate cannot
# inspect is an expression the gate cannot vouch for.
_OPAQUE_TYPES = (exp.Command, exp.Set)

# `$var` / `$1`. Session variables let a value be resolved outside the text the
# gate inspected, which defeats the point of inspecting it.
_PARAMETER_TYPES = (exp.Parameter, exp.SessionParameter)


def validate_sql(
    sql: str, allowed_tables: frozenset[str] = ALLOWED_TABLES
) -> SqlGateResult:
    """sqlglot parse with dialect='snowflake'. Enforce: parses cleanly;
    exactly one statement; statement is a SELECT (CTEs allowed, must resolve
    to SELECT); every referenced table is in allowed_tables; no banned
    constructs (DML/DDL/INTO/CALL/session vars); inject LIMIT SQL_ROW_LIMIT
    when absent. Returns sanitized SQL on ok=True.
    """
    # ---- 1. parse ------------------------------------------------------
    try:
        parsed = sqlglot.parse(sql, dialect="snowflake")
    except SqlglotError as err:
        return _reject(SqlViolation.PARSE_ERROR, f"could not parse SQL: {err}")
    except RecursionError:  # pathologically nested input
        return _reject(SqlViolation.PARSE_ERROR, "SQL is too deeply nested to parse")

    statements = [s for s in parsed if s is not None]
    if not statements:
        return _reject(SqlViolation.PARSE_ERROR, "no SQL statement found")

    # ---- 2. single statement -------------------------------------------
    if len(statements) > 1:
        return _reject(
            SqlViolation.MULTI_STATEMENT,
            f"expected exactly one statement, found {len(statements)}",
        )

    statement = statements[0]

    # ---- 3. opaque command syntax --------------------------------------
    # Checked before the query test so CALL/SET report as BANNED_CONSTRUCT
    # rather than the less specific NOT_SELECT.
    if isinstance(statement, _OPAQUE_TYPES):
        return _reject(
            SqlViolation.BANNED_CONSTRUCT,
            f"{_command_name(statement)} is not a permitted statement",
        )

    # ---- 4. must be a query --------------------------------------------
    if not _is_query(statement):
        return _reject(
            SqlViolation.NOT_SELECT,
            f"only SELECT statements are permitted, got {type(statement).__name__.upper()}",
        )

    # ---- 5 & 6. accumulate content violations --------------------------
    # Both are reported together: the agent has only MAX_RECOVERY_RETRIES
    # attempts (rule 9), so spending one per problem is wasteful.
    violations: list[SqlViolation] = []
    details: list[str] = []

    banned = _banned_constructs(statement)
    if banned:
        violations.append(SqlViolation.BANNED_CONSTRUCT)
        details.extend(banned)

    table_problem = _table_violation(statement, allowed_tables)
    if table_problem:
        violations.append(SqlViolation.TABLE_NOT_ALLOWED)
        details.append(table_problem)

    if violations:
        return SqlGateResult(
            ok=False, sql="", violations=violations, detail="; ".join(details)
        )

    # ---- 7. bound the result set ---------------------------------------
    bounded = _apply_row_limit(statement)
    return SqlGateResult(ok=True, sql=bounded.sql(dialect="snowflake"))


# --------------------------------------------------------------------------
# Statement shape
# --------------------------------------------------------------------------

def _is_query(statement: exp.Expression) -> bool:
    """A parenthesised query is a query; a parenthesised anything-else is not."""
    if isinstance(statement, exp.Subquery):
        return _is_query(statement.this) if statement.this is not None else False
    return isinstance(statement, _QUERY_TYPES)


def _command_name(statement: exp.Expression) -> str:
    if isinstance(statement, exp.Set):
        return "SET"
    name = str(statement.args.get("this") or "").strip()
    return name.upper() or "this command"


# --------------------------------------------------------------------------
# Banned constructs
# --------------------------------------------------------------------------

def _banned_constructs(statement: exp.Expression) -> list[str]:
    """Everything forbidden inside an otherwise well-formed query."""
    found: list[str] = []

    # `SELECT ... INTO` is a write wearing a SELECT's clothes — sqlglot even
    # renders it back out as CREATE TABLE AS.
    if any(True for _ in statement.find_all(exp.Into)):
        found.append("SELECT ... INTO is not permitted")

    if any(True for _ in statement.find_all(*_PARAMETER_TYPES)):
        found.append("session variables ($var) are not permitted")

    # Opaque syntax nested inside the query (e.g. a scalar subquery holding a
    # command sqlglot could not model).
    if any(True for _ in statement.find_all(*_OPAQUE_TYPES)):
        found.append("procedure calls and session commands are not permitted")

    unmodeled = _unmodeled_functions(statement)
    if unmodeled:
        found.append(
            "unrecognised function(s) not permitted: " + ", ".join(unmodeled)
        )

    if _has_star_projection(statement):
        # D-007. SQL_ROW_LIMIT protects tokens; this protects scan cost. The
        # B/C tables average ~280 columns, so `SELECT *` under LIMIT 200 still
        # drags ~56,000 cells into the model context — a query that defeats the
        # scan budget while passing every other check.
        found.append("star projection (SELECT *) is not permitted; name the columns")

    return found


def _unmodeled_functions(statement: exp.Expression) -> list[str]:
    """Names of function calls sqlglot has no typed node for.

    An allowlisted FROM clause does not launder the rest of the statement.
    Two whole classes of bypass live in the projection and WHERE clauses,
    where nothing the gate checks so far ever looks:

      SELECT a, SYSTEM$CANCEL_ALL_QUERIES() FROM ...allowed...
      SELECT GET_DDL('TABLE', '..."2019_CBG_B99"'), a FROM ...allowed...

    The first is a side effect — a write wearing a SELECT's clothes, the same
    thing `SELECT ... INTO` is banned for. The second names its target object
    with a string literal, so it produces no Table node and the allowlist
    never runs on it. An external function would be worse still: it can post
    the rows it was handed to an arbitrary endpoint.

    A denylist of known-dangerous names cannot cover UDFs or external
    functions, whose names are arbitrary. So the rule is the same default-deny
    the rest of this module uses: sqlglot models ordinary SQL functions as
    typed nodes and falls back to `Anonymous` for everything else, which makes
    "sqlglot could not model this" a usable proxy for "outside ordinary SQL".
    In practice that costs almost nothing — of 48 functions a census answer
    plausibly needs (aggregates, SUBSTR, DIV0, TO_CHAR, MEDIAN, window
    functions), 47 are typed nodes.
    """
    names = {str(node.this).upper() for node in statement.find_all(exp.Anonymous)}

    # IDENTIFIER('...') gets its own typed node, but it is the same hazard:
    # a table reference built from a string the allowlist cannot inspect.
    if any(True for _ in statement.find_all(exp.DynamicIdentifier)):
        names.add("IDENTIFIER")

    return sorted(names)


def _has_star_projection(statement: exp.Expression) -> bool:
    """True if any star in the tree widens the scan.

    The rule is stated over stars rather than over projection lists, because
    the cost D-007 is about is "how many columns get read", and that is a
    property of the star, not of where it sits. `SELECT *`, `SELECT t.*`, a
    star inside a CTE body or a derived table, and `OBJECT_CONSTRUCT(*)` —
    which reads all ~280 columns and hands them back as one JSON value — are
    the same query wearing different clothes.

    `COUNT(*)` is the sole exemption: it is the one star that reads no
    columns at all.
    """
    for star in statement.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            return True
    return False


# --------------------------------------------------------------------------
# Table allowlist
# --------------------------------------------------------------------------

def _table_violation(
    statement: exp.Expression, allowed_tables: frozenset[str]
) -> str | None:
    """None when every table reference is allowlisted and there is at least
    one of them."""
    referenced: list[str] = []
    for table in statement.find_all(exp.Table):
        key = _table_key(table)
        if key is None:
            # A table position sqlglot did not resolve to an identifier —
            # a table function, say. Unverifiable, therefore not allowed.
            referenced.append("<unresolvable table reference>")
            continue
        if "." not in key and key in _visible_cte_names(table):
            continue  # a CTE name in scope here, not a physical table
        referenced.append(key)

    if not referenced:
        # Zero table references is not "nothing to check". It is the shape
        # every escape takes that reads data without naming a table —
        # TABLE(RESULT_SCAN(...)), SYSTEM$ functions, INFORMATION_SCHEMA
        # helpers. A census answer always reads a census table.
        return "query does not read any allowed table"

    disallowed = sorted({t for t in referenced if t not in allowed_tables})
    if disallowed:
        return "table(s) not in the allowlist: " + ", ".join(disallowed)
    return None


def _visible_cte_names(table: exp.Table) -> set[str]:
    """CTE names actually in scope at this table reference.

    Scope matters, so this walks up the ancestor chain rather than collecting
    every CTE in the statement into one flat set. The flat version excuses a
    bare table name anywhere in the tree as soon as *some* branch defines a
    CTE by that name, which is a bypass:

        SELECT a FROM ...allowed...
        WHERE cbg IN (SELECT cbg FROM "2019_CBG_B01")     -- real 2019 table
        UNION ALL
        SELECT x AS a FROM (
            WITH "2019_CBG_B01" AS (SELECT 1 AS x)        -- decoy, other scope
            SELECT x FROM "2019_CBG_B01"
        ) AS decoy

    The decoy CTE is not in scope in the first branch, so Snowflake resolves
    that reference against the physical 2019 table — defeating D-003, which
    is the whole reason the allowlist is 2020-only.
    """
    ancestors = {id(node) for node in _ancestors(table)}

    names: set[str] = set()
    node: exp.Expression | None = table
    while node is not None:
        # sqlglot 30 spells the arg `with_`; older versions used `with`.
        with_clause = node.args.get("with_") or node.args.get("with")
        if isinstance(with_clause, exp.With):
            names.update(_cte_names_in_scope(with_clause, ancestors))
        node = node.parent
    return names


def _cte_names_in_scope(
    with_clause: exp.With, ancestors: set[int]
) -> set[str]:
    """CTE names from one WITH list that are visible at a reference inside it.

    Visibility follows declaration order: a CTE sees the ones declared before
    it, not the ones after. Ignoring that is the same bypass one level down —

        WITH leak AS (SELECT cbg FROM "2019_CBG_B01"),
             "2019_CBG_B01" AS (SELECT 1 AS cbg)     -- declared AFTER
        SELECT ... WHERE cbg IN (SELECT cbg FROM leak)

    `leak` cannot see a CTE declared after it, so Snowflake resolves that bare
    name against the physical 2019 table. Only `WITH RECURSIVE` lets a CTE see
    itself.
    """
    names: set[str] = set()
    recursive = bool(with_clause.args.get("recursive"))

    for cte in with_clause.expressions:
        alias = cte.args.get("alias")
        containing = id(cte) in ancestors
        if containing and not recursive:
            # The reference lives inside this CTE's body: everything declared
            # from here on is not yet in scope.
            break
        if isinstance(alias, exp.TableAlias):
            names.add(_identifier_text(alias.this))
        if containing:
            break

    return names


def _ancestors(node: exp.Expression) -> list[exp.Expression]:
    chain: list[exp.Expression] = []
    current = node.parent
    while current is not None:
        chain.append(current)
        current = current.parent
    return chain


def _table_key(table: exp.Table) -> str | None:
    """Render a Table node as `CATALOG.DB.NAME` for allowlist comparison.

    Snowflake identifier rules, applied exactly: unquoted identifiers fold to
    upper case, quoted ones are case-sensitive. So `us_census.public."2020_CBG_B01"`
    matches the allowlist and `US_CENSUS.PUBLIC."2020_cbg_b01"` does not — the
    latter names a table that genuinely does not exist.
    """
    parts: list[str] = []
    for key in ("catalog", "db", "this"):
        node = table.args.get(key)
        if node is None:
            continue
        if not isinstance(node, exp.Identifier):
            return None
        text = _identifier_text(node)
        if text:
            parts.append(text)
    return ".".join(parts) if parts else None


def _identifier_text(node: exp.Expression | None) -> str:
    if not isinstance(node, exp.Identifier):
        return ""
    name = str(node.this)
    return name if node.quoted else name.upper()


# --------------------------------------------------------------------------
# Row limit
# --------------------------------------------------------------------------

def _apply_row_limit(statement: exp.Expression) -> exp.Expression:
    """Guarantee the statement is bounded by at most SQL_ROW_LIMIT rows.

    An explicit smaller limit is left exactly as written — the agent may have
    a reason to want five rows, and rewriting it would be the gate editing
    intent rather than enforcing a bound. A larger one is clamped: the cap is
    a ceiling, not a default, and a gate that honours `LIMIT 100000` bounds
    nothing.

    sqlglot normalises Snowflake's `FETCH FIRST n ROWS ONLY` into the same
    `limit` slot, so it is handled here too without a special case.
    """
    current = statement.args.get("limit")

    if current is None:
        return statement.limit(SQL_ROW_LIMIT)

    if isinstance(current, exp.Fetch):
        options = current.args.get("limit_options")
        # FETCH FIRST 10 PERCENT is a share of the result set, not a row
        # count, and WITH TIES can overshoot. Neither is a bound we can read,
        # so both get replaced by the cap.
        if isinstance(options, exp.LimitOptions) and (
            options.args.get("percent") or options.args.get("with_ties")
        ):
            return statement.limit(SQL_ROW_LIMIT)
        count = current.args.get("count")
    else:
        count = current.args.get("expression")

    if isinstance(count, exp.Literal) and count.is_int:
        if int(count.this) <= SQL_ROW_LIMIT:
            return statement
        return statement.limit(SQL_ROW_LIMIT)

    # A non-literal bound (an expression, a cast) cannot be checked by reading
    # it, so replace it with the cap.
    return statement.limit(SQL_ROW_LIMIT)


# --------------------------------------------------------------------------
# Result helpers
# --------------------------------------------------------------------------

def _reject(violation: SqlViolation, detail: str) -> SqlGateResult:
    """`sql` is always empty on rejection: a caller that forgets to check
    `ok` must not find something runnable in it."""
    return SqlGateResult(ok=False, sql="", violations=[violation], detail=detail)
