"""Snapshot builder — src/contracts.py:build_snapshot (issue #2).

Pulls ACS variable metadata and FIPS geography from Snowflake into local
SQLite at startup so search_census_variables/resolve_geography never touch
Snowflake at request time (CLAUDE.md rule 13). This module is the one
exception to "Snowflake only through run_census_sql" (contracts.py rule 1) —
that rule scopes to request time; snapshot building runs once at boot with a
fixed, developer-authored query, never user text.

Two schema-notes.md traps this file guards against (docs/schema-notes.md §4,
§5 trap 3):
  * Snowflake's CONCAT_WS returns NULL if *any* argument is NULL, unlike
    Postgres/MySQL — every FIELD_LEVEL_* column must be COALESCE-wrapped or
    the breadcrumb text (and the whole indexed row) silently disappears.
  * The 9th breadcrumb column has an upstream typo, `FIELD_LEVELl_9`
    (lowercase l before the 9), verified via DESCRIBE TABLE. The "corrected"
    spelling raises `invalid identifier` in Snowflake.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.contracts import SnapshotError, SnapshotInfo
from src.snowflake_conn import connect as _connect

SNAPSHOT_DB_PATH = Path(os.environ.get("SNAPSHOT_DB_PATH", "data/snapshot.sqlite3"))

VARIABLES_TABLE = 'US_CENSUS.PUBLIC."2020_METADATA_CBG_FIELD_DESCRIPTIONS"'
FIPS_TABLE = 'US_CENSUS.PUBLIC."2020_METADATA_CBG_FIPS_CODES"'

# Upstream typo hardcoded verbatim (schema-notes §4) — do not "fix" the l.
_BREADCRUMB_COLUMNS = [f"FIELD_LEVEL_{n}" for n in range(1, 9)] + [
    '"FIELD_LEVELl_9"',
    "FIELD_LEVEL_10",
]

_ESTIMATE_SUFFIX = re.compile(r"e\d+$")


def _variables_query() -> str:
    coalesced_breadcrumbs = ", ".join(
        f"COALESCE({col}, '')" for col in _BREADCRUMB_COLUMNS
    )
    searchable_text = (
        "CONCAT_WS(' ', COALESCE(TABLE_TITLE, ''), "
        f"{coalesced_breadcrumbs}, COALESCE(TABLE_UNIVERSE, '')) AS SEARCHABLE_TEXT"
    )
    return (
        "SELECT TABLE_ID, TABLE_NUMBER, TABLE_TITLE, TABLE_UNIVERSE, "
        f"{searchable_text} FROM {VARIABLES_TABLE}"
    )


def _geography_query() -> str:
    return f"SELECT STATE, STATE_FIPS, COUNTY, COUNTY_FIPS, CLASS_CODE FROM {FIPS_TABLE}"


def _should_index_variable(table_number: str, table_id: str) -> bool:
    """D-008: exclude B99* allocation tables and m-suffixed MOE rows. Only
    e-suffixed estimate rows are indexed (~3,300 of 8,164)."""
    if table_number.startswith("B99"):
        return False
    return _ESTIMATE_SUFFIX.search(table_id) is not None


def _rows_as_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _expand_geo_rows(county_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """County rows are the FIPS table's native grain; state rows are
    synthesized by deduplicating on STATE_FIPS (2020_METADATA_CBG_FIPS_CODES
    carries no separate state-level rows)."""
    expanded: list[dict[str, Any]] = []
    seen_states: set[str] = set()

    for row in county_rows:
        state_fips = row["STATE_FIPS"]
        county_fips = row["COUNTY_FIPS"]
        expanded.append(
            {
                "level": "county",
                "geo_id": f"{state_fips}{county_fips}",
                "name": f"{row['COUNTY']}, {row['STATE']}",
                "state": row["STATE"],
                "state_fips": state_fips,
                "county": row["COUNTY"],
                "county_fips": county_fips,
                "class_code": row["CLASS_CODE"],
            }
        )
        if state_fips not in seen_states:
            seen_states.add(state_fips)
            expanded.append(
                {
                    "level": "state",
                    "geo_id": state_fips,
                    "name": row["STATE"],
                    "state": row["STATE"],
                    "state_fips": state_fips,
                    "county": None,
                    "county_fips": None,
                    "class_code": None,
                }
            )

    return expanded


def _write_sqlite(
    path: Path,
    variable_rows: list[dict[str, Any]],
    geo_rows: list[dict[str, Any]],
    built_at: datetime,
    elapsed_s: float,
    source_tables: list[str],
) -> tuple[int, int]:
    """Builds into a temp file and atomically renames onto `path` so a crash
    or exception mid-build never leaves a partially-written snapshot behind
    for the next boot's no-op path to trip over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE variables_fts USING fts5("
            "variable_id UNINDEXED, table_number UNINDEXED, label, "
            "universe, searchable_text)"
        )
        conn.executemany(
            "INSERT INTO variables_fts "
            "(variable_id, table_number, label, universe, searchable_text) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["TABLE_ID"],
                    row["TABLE_NUMBER"],
                    row["TABLE_TITLE"],
                    row["TABLE_UNIVERSE"],
                    row["SEARCHABLE_TEXT"],
                )
                for row in variable_rows
            ],
        )

        geo = _expand_geo_rows(geo_rows)
        conn.execute(
            "CREATE TABLE geography ("
            "level TEXT NOT NULL, geo_id TEXT NOT NULL, name TEXT NOT NULL, "
            "state TEXT NOT NULL, state_fips TEXT NOT NULL, "
            "county TEXT, county_fips TEXT, class_code TEXT)"
        )
        conn.executemany(
            "INSERT INTO geography "
            "(level, geo_id, name, state, state_fips, county, county_fips, class_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["level"],
                    row["geo_id"],
                    row["name"],
                    row["state"],
                    row["state_fips"],
                    row["county"],
                    row["county_fips"],
                    row["class_code"],
                )
                for row in geo
            ],
        )

        # Written last: its presence marks the file as a complete build, not
        # a partial one. Read back by the no-op path below.
        conn.execute(
            "CREATE TABLE snapshot_meta "
            "(built_at TEXT NOT NULL, elapsed_s REAL NOT NULL, source_tables TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO snapshot_meta (built_at, elapsed_s, source_tables) VALUES (?, ?, ?)",
            (built_at.isoformat(), elapsed_s, ",".join(source_tables)),
        )

        conn.commit()
    except Exception:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        raise
    else:
        conn.close()

    tmp_path.replace(path)
    return len(variable_rows), len(geo)


def _read_existing_snapshot_info(path: Path) -> SnapshotInfo:
    """Reads back the metadata a prior `_write_sqlite` call persisted. Any
    failure here (missing table, corrupt file) means the existing snapshot
    can't be trusted — surfaced as SnapshotError, never an unhandled
    exception, so a bad prior build doesn't crash the next boot."""
    try:
        conn = sqlite3.connect(path)
        try:
            variables_rows = conn.execute("SELECT COUNT(*) FROM variables_fts").fetchone()[0]
            geo_rows = conn.execute("SELECT COUNT(*) FROM geography").fetchone()[0]
            built_at, elapsed_s, source_tables = conn.execute(
                "SELECT built_at, elapsed_s, source_tables FROM snapshot_meta"
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        raise SnapshotError(f"existing snapshot at {path} is unreadable: {exc}") from exc

    return SnapshotInfo(
        built_at=datetime.fromisoformat(built_at),
        variables_rows=variables_rows,
        geo_rows=geo_rows,
        elapsed_s=elapsed_s,
        source_tables=source_tables.split(",") if source_tables else [],
    )


def build_snapshot(force: bool = False) -> SnapshotInfo:
    """Pull the attributes/metadata table and geography index from Snowflake
    into local SQLite (FTS5 over variable label+description; plain index over
    geo names). Runs at app startup; no-op when a snapshot exists and
    force=False. Raises SnapshotError on failure — caller boots degraded.
    """
    if not force and SNAPSHOT_DB_PATH.exists():
        return _read_existing_snapshot_info(SNAPSHOT_DB_PATH)

    start = time.monotonic()
    try:
        conn = _connect()
        try:
            cursor = conn.cursor()
            cursor.execute(_variables_query())
            variable_rows = _rows_as_dicts(cursor)

            cursor.execute(_geography_query())
            geo_rows = _rows_as_dicts(cursor)
        finally:
            conn.close()
    except Exception as exc:
        raise SnapshotError(f"snapshot build failed: {exc}") from exc

    indexed_variable_rows = [
        row
        for row in variable_rows
        if _should_index_variable(row["TABLE_NUMBER"], row["TABLE_ID"])
    ]

    built_at = datetime.now(timezone.utc)
    elapsed_s = time.monotonic() - start
    source_tables = [VARIABLES_TABLE, FIPS_TABLE]

    try:
        variables_n, geo_n = _write_sqlite(
            SNAPSHOT_DB_PATH,
            indexed_variable_rows,
            geo_rows,
            built_at,
            elapsed_s,
            source_tables,
        )
    except Exception as exc:
        raise SnapshotError(f"snapshot write failed: {exc}") from exc

    return SnapshotInfo(
        built_at=built_at,
        variables_rows=variables_n,
        geo_rows=geo_n,
        elapsed_s=elapsed_s,
        source_tables=source_tables,
    )
