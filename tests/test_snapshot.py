"""Tests for the snapshot builder — src/contracts.py:build_snapshot (issue #2).

Pulls ACS variable metadata + FIPS geography from Snowflake into local
SQLite at startup so search_census_variables/resolve_geography never touch
Snowflake at request time (CLAUDE.md rule 13). Written first per rule 19.

Two traps this file guards against (docs/schema-notes.md §4, §5 traps):
  * Snowflake's CONCAT_WS returns NULL if *any* argument is NULL, unlike
    Postgres/MySQL — every FIELD_LEVEL_* column must be COALESCE-wrapped or
    the breadcrumb text (and the whole indexed row) silently disappears.
  * The 9th breadcrumb column has an upstream typo, `FIELD_LEVELl_9`
    (lowercase l before the 9) — the "corrected" spelling raises
    `invalid identifier` in Snowflake.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from src.contracts import SnapshotError, SnapshotInfo
from src.snapshot import (
    FIPS_TABLE,
    VARIABLES_TABLE,
    _geography_query,
    _should_index_variable,
    _variables_query,
    build_snapshot,
)
import src.snapshot as snapshot

# --------------------------------------------------------------------------
# Fakes — no real Snowflake connection in tests
# --------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, results: dict[str, tuple[list[str], list[tuple]]]):
        self._results = results
        self.description: list[tuple] = []
        self._rows: list[tuple] = []

    def execute(self, sql: str) -> None:
        for marker, (columns, rows) in self._results.items():
            if marker in sql:
                self.description = [(c,) for c in columns]
                self._rows = rows
                return
        raise AssertionError(f"FakeCursor got an unrecognized query: {sql!r}")

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, results: dict[str, tuple[list[str], list[tuple]]]):
        self._results = results
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._results)

    def close(self) -> None:
        self.closed = True


def variable_row_columns() -> list[str]:
    return ["TABLE_ID", "TABLE_NUMBER", "TABLE_TITLE", "TABLE_UNIVERSE", "SEARCHABLE_TEXT"]


def geo_row_columns() -> list[str]:
    return ["STATE", "STATE_FIPS", "COUNTY", "COUNTY_FIPS", "CLASS_CODE"]


def make_fake_connect(
    variable_rows: list[tuple],
    geo_rows: list[tuple],
    raises: Exception | None = None,
):
    def _connect():
        if raises is not None:
            raise raises
        return FakeConnection(
            {
                "FROM " + VARIABLES_TABLE: (variable_row_columns(), variable_rows),
                "FROM " + FIPS_TABLE: (geo_row_columns(), geo_rows),
            }
        )

    return _connect


@pytest.fixture(autouse=True)
def isolated_snapshot_path(tmp_path, monkeypatch):
    """Every test gets its own snapshot file; nothing touches the real path."""
    monkeypatch.setattr(snapshot, "SNAPSHOT_DB_PATH", tmp_path / "snapshot.sqlite3")


# --------------------------------------------------------------------------
# Query-text regression tests (pure functions — no I/O)
# --------------------------------------------------------------------------


def test_every_breadcrumb_column_is_coalesce_wrapped():
    """Unguarded CONCAT_WS with a NULL arg returns NULL for the whole row
    (schema-notes §5 trap 3). Every FIELD_LEVEL_n reference in the query
    must sit inside a COALESCE(...) call."""
    query = _variables_query()

    breadcrumb_refs = re.findall(r'COALESCE\(("?FIELD_LEVEL\w*"?),', query)
    # 8 plain breadcrumb columns + the quoted 9th + the 10th = 10 total
    assert len(breadcrumb_refs) == 10, (
        f"expected 10 COALESCE-wrapped breadcrumb columns, found {breadcrumb_refs} "
        f"in query:\n{query}"
    )

    # Bare, unguarded FIELD_LEVEL_n directly inside CONCAT_WS (not preceded by
    # "COALESCE(") is exactly the regression this test exists to catch.
    unguarded = re.findall(r'(?<!COALESCE\()(?<!\w)FIELD_LEVEL_\d+\b', query)
    assert not unguarded, f"found unguarded breadcrumb column(s): {unguarded}"


def test_field_level_l_9_typo_used_verbatim():
    """docs/schema-notes.md §4: the 9th breadcrumb column is `FIELD_LEVELl_9`
    (lowercase l), verified via DESCRIBE TABLE. The "corrected" spelling
    raises `invalid identifier` in Snowflake — must never appear."""
    query = _variables_query()

    assert '"FIELD_LEVELl_9"' in query
    # The corrected-but-wrong spelling must not appear anywhere, including
    # as a bare identifier the typo'd column could be confused with.
    assert re.search(r"\bFIELD_LEVEL_9\b", query) is None


def test_variables_query_selects_from_field_descriptions_table():
    assert VARIABLES_TABLE in _variables_query()


def test_geography_query_selects_fips_table_not_geographic_data():
    query = _geography_query()
    assert FIPS_TABLE in query
    assert "GEOGRAPHIC_DATA" not in query


# --------------------------------------------------------------------------
# Row-filtering regression tests (pure function — no I/O)
# --------------------------------------------------------------------------


def test_b99_allocation_tables_excluded():
    assert _should_index_variable(table_number="B99102", table_id="B99102e1") is False


def test_non_b99_table_included():
    assert _should_index_variable(table_number="B19013", table_id="B19013e1") is True


def test_moe_suffixed_row_excluded():
    assert _should_index_variable(table_number="B19013", table_id="B19013m1") is False


def test_estimate_suffixed_row_included():
    assert _should_index_variable(table_number="B19013", table_id="B19013e1") is True


# --------------------------------------------------------------------------
# build_snapshot — end-to-end against a fake Snowflake connection
# --------------------------------------------------------------------------


def test_build_snapshot_excludes_b99_and_moe_rows_from_built_corpus(monkeypatch):
    variable_rows = [
        ("B19013e1", "B19013", "Median Household Income", "Households", "income text"),
        ("B19013m1", "B19013", "Median Household Income", "Households", "income moe text"),
        ("B99102e1", "B99102", "Allocation Of Grandparents", "Total population", "allocation text"),
    ]
    geo_rows = [("Illinois", "17", "Cook County", "031", "H1")]
    monkeypatch.setattr(
        snapshot, "_connect", make_fake_connect(variable_rows, geo_rows)
    )

    info = build_snapshot()

    assert isinstance(info, SnapshotInfo)
    assert info.variables_rows == 1

    conn = sqlite3.connect(snapshot.SNAPSHOT_DB_PATH)
    indexed_ids = {
        row[0] for row in conn.execute("SELECT variable_id FROM variables_fts")
    }
    conn.close()
    assert indexed_ids == {"B19013e1"}


def test_build_snapshot_excludes_null_state_territory_rows(monkeypatch):
    """13 real FIPS rows (American Samoa, Guam, N. Mariana Islands, USVI)
    have STATE = NULL — county-equivalent name only, no state name. Found
    against live Snowflake, not covered by any prior synthetic fixture."""
    variable_rows = [("B19013e1", "B19013", "Median Household Income", "Households", "text")]
    geo_rows = [
        ("Illinois", "17", "Cook County", "031", "H1"),
        (None, "60", "Eastern District", "010", "H1"),
    ]
    monkeypatch.setattr(snapshot, "_connect", make_fake_connect(variable_rows, geo_rows))

    info = build_snapshot()

    conn = sqlite3.connect(snapshot.SNAPSHOT_DB_PATH)
    rows = conn.execute("SELECT geo_id, state FROM geography").fetchall()
    conn.close()

    assert ("17031", "Illinois") in rows
    assert not any(geo_id.startswith("60") for geo_id, _ in rows)
    assert info.geo_rows == 2


def test_build_snapshot_writes_county_and_state_geo_rows(monkeypatch):
    variable_rows = [("B19013e1", "B19013", "Median Household Income", "Households", "text")]
    geo_rows = [
        ("Illinois", "17", "Cook County", "031", "H1"),
        ("Illinois", "17", "DuPage County", "043", "H1"),
        ("Indiana", "18", "Lake County", "089", "H1"),
    ]
    monkeypatch.setattr(snapshot, "_connect", make_fake_connect(variable_rows, geo_rows))

    info = build_snapshot()

    conn = sqlite3.connect(snapshot.SNAPSHOT_DB_PATH)
    levels = [row[0] for row in conn.execute("SELECT level FROM geography")]
    conn.close()

    # 3 county rows + 2 distinct states (Illinois, Indiana)
    assert levels.count("county") == 3
    assert levels.count("state") == 2
    assert info.geo_rows == 5


def test_build_snapshot_raises_snapshot_error_on_connection_failure(monkeypatch):
    monkeypatch.setattr(
        snapshot, "_connect", make_fake_connect([], [], raises=RuntimeError("warehouse down"))
    )

    with pytest.raises(SnapshotError):
        build_snapshot()


def test_build_snapshot_is_noop_when_file_exists_and_not_forced(monkeypatch):
    variable_rows = [("B19013e1", "B19013", "Median Household Income", "Households", "text")]
    geo_rows = [("Illinois", "17", "Cook County", "031", "H1")]
    monkeypatch.setattr(snapshot, "_connect", make_fake_connect(variable_rows, geo_rows))
    build_snapshot(force=True)  # seed a real, complete snapshot file

    def _explode():
        raise AssertionError("_connect must not be called on a force=False no-op")

    monkeypatch.setattr(snapshot, "_connect", _explode)

    info = build_snapshot(force=False)

    assert isinstance(info, SnapshotInfo)
    assert info.variables_rows == 1
    assert info.source_tables == [VARIABLES_TABLE, FIPS_TABLE]


def test_build_snapshot_force_rebuilds_existing_file(monkeypatch):
    old_variable_rows = [("B19013e1", "B19013", "Median Household Income", "Households", "text")]
    old_geo_rows = [("Illinois", "17", "Cook County", "031", "H1")]
    monkeypatch.setattr(snapshot, "_connect", make_fake_connect(old_variable_rows, old_geo_rows))
    build_snapshot(force=True)

    new_variable_rows = [
        ("B19013e1", "B19013", "Median Household Income", "Households", "text"),
        ("B01001e1", "B01001", "Sex By Age", "Total population", "text2"),
    ]
    new_geo_rows = [("Illinois", "17", "Cook County", "031", "H1")]
    monkeypatch.setattr(snapshot, "_connect", make_fake_connect(new_variable_rows, new_geo_rows))

    info = build_snapshot(force=True)

    assert info.variables_rows == 2


def test_build_snapshot_write_failure_raises_snapshot_error(monkeypatch):
    variable_rows = [("B19013e1", "B19013", "Median Household Income", "Households", "text")]
    geo_rows = [("Illinois", "17", "Cook County", "031", "H1")]
    monkeypatch.setattr(snapshot, "_connect", make_fake_connect(variable_rows, geo_rows))

    def _explode(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(snapshot, "_write_sqlite", _explode)

    with pytest.raises(SnapshotError):
        build_snapshot()

    # The partial/failed write must not leave a file for the no-op path to
    # trip over on the next boot.
    assert not snapshot.SNAPSHOT_DB_PATH.exists()


def test_build_snapshot_noop_path_raises_snapshot_error_on_corrupt_existing_file(monkeypatch):
    """A prior failed build (or manual tampering) left a SQLite file missing
    the snapshot_meta completeness marker. The no-op path must convert that
    into SnapshotError, not an unhandled sqlite3 exception — CLAUDE.md rule
    13: the app boots degraded, it never crashes on startup."""
    snapshot.SNAPSHOT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(snapshot.SNAPSHOT_DB_PATH)
    conn.execute("CREATE VIRTUAL TABLE variables_fts USING fts5(variable_id)")
    conn.execute("CREATE TABLE geography (level TEXT)")
    conn.commit()
    conn.close()

    def _explode():
        raise AssertionError("_connect must not be called on a force=False no-op")

    monkeypatch.setattr(snapshot, "_connect", _explode)

    with pytest.raises(SnapshotError):
        build_snapshot(force=False)
