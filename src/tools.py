"""The agent's three tools (CLAUDE.md rule 4) — architecture §4.

search_census_variables and resolve_geography read the local SQLite
snapshot built by src/snapshot.py only; run_census_sql is the sole
request-time Snowflake touchpoint (CLAUDE.md rule 13). Contracts frozen in
src/contracts.py; these are the real implementations consumers import
(mirrors src/sqlgate.py's relationship to contracts.py's validate_sql stub).
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

from sqlglot import exp, parse_one

from src.contracts import (
    ALLOWED_TABLES,
    DEFAULT_VINTAGE,
    SQL_ROW_LIMIT,
    SQL_STATEMENT_TIMEOUT_S,
    GeoCandidate,
    GeoLevel,
    GeoResolution,
    QueryResult,
    SqlRejected,
    VariableHit,
    VariableSearchResult,
    normalize_value,
)
from src import snapshot as _snapshot
from src.snowflake_conn import connect as _connect
from src.sqlgate import validate_sql
from src.us_states import NAME_TO_ABBR

_ALL_GEO_LEVELS: list[GeoLevel] = [
    GeoLevel.NATION,
    GeoLevel.STATE,
    GeoLevel.COUNTY,
    GeoLevel.TRACT,
    GeoLevel.BLOCK_GROUP,
]

_VARIABLE_ID_RE = re.compile(r"^[A-Z]+\d+[A-Z]\d+$", re.IGNORECASE)


def _normalize_state(state: str) -> str:
    """The geography table's `state` column is the two-letter postal
    abbreviation (verified against live Snowflake — schema-notes.md's
    "state/county FIPS -> names" description is misleading here). A caller
    may type either form ("California" or "CA"); normalize to the
    abbreviation before matching. Falls back to the input unchanged when
    it's neither a known name nor a 2-letter code, so an unmatched state
    fails the lookup honestly rather than raising."""
    stripped = state.strip()
    if len(stripped) == 2:
        return stripped.upper()
    return NAME_TO_ABBR.get(stripped.lower(), stripped)


def _geo_levels_for(label: str) -> list[GeoLevel]:
    """C-3/D-009: medians never roll up above block-group; count variables
    are valid at all five levels."""
    if "median" in label.lower():
        return [GeoLevel.BLOCK_GROUP]
    return list(_ALL_GEO_LEVELS)


def _physical_table_for_acs_variable(variable_id: str) -> str:
    unquoted = f"US_CENSUS.PUBLIC.{DEFAULT_VINTAGE}_CBG_{variable_id[:3]}"
    if unquoted not in ALLOWED_TABLES:
        raise ValueError(f"no allowlisted physical table for {variable_id}")
    prefix, table = unquoted.rsplit(".", 1)
    return f'{prefix}."{table}"'


def _fts_match_query(text: str, operator: str = "AND") -> str:
    """Token-AND by default — the retrieval strategy the FTS-viability probe
    (docs/schema-notes.md Appendix A, "Token-AND (FTS-like)" column) actually
    validated over embeddings, at 7/7 recall on in-dataset probes.

    The probe's queries were drawn from in-corpus vocabulary, which is what
    made AND look free. Real agent queries are not: they carry framing words
    ("number of", "count", "distribution") and guessed table ids that Census
    labels never contain, and under AND a single such token zeroes the entire
    result. See `operator="OR"` at the one call site that uses it."""
    tokens = re.findall(r"\w+", text)
    joiner = " " if operator == "AND" else f" {operator} "
    return joiner.join(f'"{t}"' for t in tokens)


def search_census_variables(query: str, limit: int = 10) -> VariableSearchResult:
    """FTS5 search over the local variable snapshot (issue #3). Local only —
    never opens a Snowflake connection."""
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return VariableSearchResult(query=query, hits=[], truncated=False)

    sql = (
        "SELECT variable_id, label, universe, bm25(variables_fts) AS rank "
        "FROM variables_fts WHERE variables_fts MATCH ? "
        "ORDER BY rank LIMIT ?"
    )

    conn = sqlite3.connect(_snapshot.SNAPSHOT_DB_PATH)
    try:
        rows = conn.execute(sql, (_fts_match_query(query), limit + 1)).fetchall()
        # D-020: relax to token-OR only when AND found nothing. Strictly
        # additive — a query that already matched keeps its exact hits and
        # ranking, so precision on working queries is untouched while a
        # dead-end returns something the model can refine against instead of
        # burning a tool-loop iteration on an empty result. Measured on the
        # real snapshot: where AND matched, OR returned an identical top-3 in
        # the same order, so AND-first costs nothing but is kept because it
        # is the path the Appendix A probe validated.
        if not rows:
            rows = conn.execute(
                sql, (_fts_match_query(query, operator="OR"), limit + 1)
            ).fetchall()
    finally:
        conn.close()

    truncated = len(rows) > limit
    rows = rows[:limit]

    hits = [
        VariableHit(
            variable_id=variable_id,
            physical_table=_physical_table_for_acs_variable(variable_id),
            label=label,
            description=universe or "",
            geo_levels=_geo_levels_for(label),
            years=[DEFAULT_VINTAGE],
            score=-rank,
            source="acs",
        )
        for variable_id, label, universe, rank in rows
    ]
    return VariableSearchResult(query=query, hits=hits, truncated=truncated)


def resolve_geography(name: str, level_hint: GeoLevel | None = None) -> GeoResolution:
    """Lookup over the local geography index (issue #4). Local only — never
    opens a Snowflake connection. D-002: 2+ matches -> ambiguous=True, the
    agent asks rather than silently picking."""
    name = name.strip()
    if not name:
        return GeoResolution(query=name, candidates=[], ambiguous=False)

    if "," in name:
        county_part, state_part = (p.strip() for p in name.split(",", 1))
    else:
        county_part, state_part = name, None

    rows: list[tuple[str, str, str, str]] = []
    conn = sqlite3.connect(_snapshot.SNAPSHOT_DB_PATH)
    try:
        if level_hint in (None, GeoLevel.STATE):
            for geo_id, geo_name, state in conn.execute(
                "SELECT geo_id, name, state FROM geography "
                "WHERE level = 'state' AND (LOWER(name) = LOWER(?) OR state = ?)",
                (name, _normalize_state(name)),
            ):
                rows.append((geo_id, geo_name, "state", state))

        if level_hint in (None, GeoLevel.COUNTY):
            if state_part:
                county_query = conn.execute(
                    "SELECT geo_id, name, state FROM geography "
                    "WHERE level = 'county' AND LOWER(county) = LOWER(?) "
                    "AND state = ?",
                    (county_part, _normalize_state(state_part)),
                )
            else:
                county_query = conn.execute(
                    "SELECT geo_id, name, state FROM geography "
                    "WHERE level = 'county' AND LOWER(county) = LOWER(?)",
                    (county_part,),
                )
            for geo_id, geo_name, state in county_query:
                rows.append((geo_id, geo_name, "county", state))
    finally:
        conn.close()

    candidates = [
        GeoCandidate(geo_id=geo_id, name=geo_name, level=GeoLevel(level), state=state)
        for geo_id, geo_name, level, state in rows
    ]
    # Deterministic regardless of SQLite row order.
    candidates.sort(key=lambda c: (c.state or "", c.geo_id))

    return GeoResolution(
        query=name, candidates=candidates, ambiguous=len(candidates) > 1
    )


def _projection_variable_ids(sql: str) -> dict[str, str]:
    tree = parse_one(sql, read="snowflake")

    def contributing_selects(node: exp.Expression) -> list[exp.Select]:
        while isinstance(node, exp.Subquery):
            node = node.this
        if isinstance(node, exp.SetOperation):
            return contributing_selects(node.this) + contributing_selects(
                node.expression
            )
        return [node] if isinstance(node, exp.Select) else []

    branches = contributing_selects(tree)
    if not branches:
        return {}

    projections = [branch.expressions for branch in branches]
    if not projections or any(
        len(items) != len(projections[0]) for items in projections
    ):
        return {}

    result: dict[str, str] = {}
    for index, first in enumerate(projections[0]):
        columns = []
        for items in projections:
            projection = items[index]
            column = (
                projection
                if isinstance(projection, exp.Column)
                else projection.this
                if isinstance(projection, exp.Alias)
                and isinstance(projection.this, exp.Column)
                else None
            )
            if column is None or not _VARIABLE_ID_RE.fullmatch(column.name):
                break
            columns.append(column.name)
        if columns and len(columns) == len(projections) and len(set(columns)) == 1:
            result[first.alias_or_name.upper()] = columns[0]
    return result


def _presentation_value(
    column: str,
    raw: Any,
    variable_ids: dict[str, str],
) -> Any:
    variable_id = variable_ids.get(column.upper())
    if variable_id is None:
        return raw
    normalized = normalize_value(raw, variable_id)
    if normalized.suppressed:
        return "not reported"
    if normalized.top_coded:
        return "$250,000 or more"
    return raw


def run_census_sql(sql: str) -> QueryResult:
    """The ONLY code path that touches Snowflake at request time (issue #5,
    CLAUDE.md rule 13). validate_sql -> execute with STATEMENT_TIMEOUT_IN_SECONDS
    set on the session -> QueryResult. Never executes anything but the
    gate's sanitized output."""
    gate_result = validate_sql(sql)
    if not gate_result.ok:
        raise SqlRejected(gate_result)

    start = time.monotonic()
    conn = _connect(
        session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": SQL_STATEMENT_TIMEOUT_S}
    )
    try:
        cursor = conn.cursor()
        cursor.execute(gate_result.sql)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows: list[Any] = cursor.fetchall()
    finally:
        conn.close()

    elapsed_ms = int((time.monotonic() - start) * 1000)
    variable_ids = _projection_variable_ids(gate_result.sql)
    row_dicts = [
        {
            column: _presentation_value(column, raw, variable_ids)
            for column, raw in zip(columns, row)
        }
        for row in rows
    ]
    return QueryResult(
        columns=columns,
        rows=row_dicts,
        row_count=len(row_dicts),
        truncated=len(row_dicts) >= SQL_ROW_LIMIT,
        elapsed_ms=elapsed_ms,
    )
