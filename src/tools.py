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

from src.contracts import (
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
)
from src import snapshot as _snapshot
from src.snowflake_conn import connect as _connect
from src.sqlgate import validate_sql

_ALL_GEO_LEVELS: list[GeoLevel] = [
    GeoLevel.NATION,
    GeoLevel.STATE,
    GeoLevel.COUNTY,
    GeoLevel.TRACT,
    GeoLevel.BLOCK_GROUP,
]

# USPS postal abbreviations — public, static reference data (not PROVISIONAL;
# unrelated to the Snowflake schema-notes recon). Falls back to the full name
# for any state not in this table rather than raising.
_STATE_POSTAL_ABBR: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}


def _geo_levels_for(label: str) -> list[GeoLevel]:
    """C-3/D-009: medians never roll up above block-group; count variables
    are valid at all five levels."""
    if "median" in label.lower():
        return [GeoLevel.BLOCK_GROUP]
    return list(_ALL_GEO_LEVELS)


def _fts_match_query(text: str) -> str:
    """Token-AND, not OR — this is the retrieval strategy the FTS-viability
    probe (docs/schema-notes.md Appendix A, "Token-AND (FTS-like)" column)
    actually validated over embeddings, at 7/7 recall on in-dataset probes.
    OR would rank a single-rare-term match above the true multi-term hit."""
    tokens = re.findall(r"\w+", text)
    return " ".join(f'"{t}"' for t in tokens)


def search_census_variables(query: str, limit: int = 10) -> VariableSearchResult:
    """FTS5 search over the local variable snapshot (issue #3). Local only —
    never opens a Snowflake connection."""
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return VariableSearchResult(query=query, hits=[], truncated=False)

    conn = sqlite3.connect(_snapshot.SNAPSHOT_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT variable_id, label, universe, bm25(variables_fts) AS rank "
            "FROM variables_fts WHERE variables_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (_fts_match_query(query), limit + 1),
        ).fetchall()
    finally:
        conn.close()

    truncated = len(rows) > limit
    rows = rows[:limit]

    hits = [
        VariableHit(
            variable_id=variable_id,
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
                "WHERE level = 'state' AND LOWER(name) = LOWER(?)",
                (name,),
            ):
                rows.append((geo_id, geo_name, "state", state))

        if level_hint in (None, GeoLevel.COUNTY):
            if state_part:
                county_query = conn.execute(
                    "SELECT geo_id, name, state FROM geography "
                    "WHERE level = 'county' AND LOWER(county) = LOWER(?) "
                    "AND LOWER(state) = LOWER(?)",
                    (county_part, state_part),
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
        GeoCandidate(
            geo_id=geo_id,
            name=geo_name,
            level=GeoLevel(level),
            state=_STATE_POSTAL_ABBR.get(state, state),
        )
        for geo_id, geo_name, level, state in rows
    ]
    # Deterministic regardless of SQLite row order.
    candidates.sort(key=lambda c: (c.state or "", c.geo_id))

    return GeoResolution(
        query=name, candidates=candidates, ambiguous=len(candidates) > 1
    )


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
    row_dicts = [dict(zip(columns, row)) for row in rows]
    return QueryResult(
        columns=columns,
        rows=row_dicts,
        row_count=len(row_dicts),
        truncated=len(row_dicts) >= SQL_ROW_LIMIT,
        elapsed_ms=elapsed_ms,
    )
