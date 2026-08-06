"""Tests for the agent's three tools (CLAUDE.md rule 4) — issues #3, #4, #5.

search_census_variables and resolve_geography read the local SQLite
snapshot only (CLAUDE.md rule 13); run_census_sql is the sole request-time
Snowflake touchpoint. Written first per CLAUDE.md rule 19.

The county-collision fixture (Washington/Jefferson/Franklin) uses synthetic
state names, not real US geography — this suite has no live Snowflake
connection to pull the real FIPS table from, and the property under test is
resolve_geography's ambiguity/ranking logic, not a reproduction of real
county-name counts (those are exercised by the golden evals against the
real snapshot, per PRD §7 AMB-01/02).
"""

from __future__ import annotations

import pytest

from src.contracts import (
    SQL_STATEMENT_TIMEOUT_S,
    GeoLevel,
    QueryResult,
    SqlRejected,
)
import src.snapshot as snapshot
import src.tools as tools

# --------------------------------------------------------------------------
# Fixture snapshot — seeded through the real build_snapshot pipeline against
# a fake Snowflake connection, so search/resolve exercise real FTS5/SQLite
# behavior rather than hand-mocked query results.
# --------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, results):
        self._results = results
        self.description = []
        self._rows = []

    def execute(self, sql):
        for marker, (columns, rows) in self._results.items():
            if marker in sql:
                self.description = [(c,) for c in columns]
                self._rows = rows
                return
        raise AssertionError(f"FakeCursor got an unrecognized query: {sql!r}")

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, results):
        self._results = results

    def cursor(self):
        return FakeCursor(self._results)

    def close(self):
        pass


def _make_fake_snowflake_connect(variable_rows, geo_rows):
    def _connect():
        return FakeConnection(
            {
                "FROM " + snapshot.VARIABLES_TABLE: (
                    ["TABLE_ID", "TABLE_NUMBER", "TABLE_TITLE", "TABLE_UNIVERSE", "SEARCHABLE_TEXT"],
                    variable_rows,
                ),
                "FROM " + snapshot.FIPS_TABLE: (
                    ["STATE", "STATE_FIPS", "COUNTY", "COUNTY_FIPS", "CLASS_CODE"],
                    geo_rows,
                ),
            }
        )

    return _connect


def _default_variable_rows():
    return [
        ("B01003e1", "B01003", "Total Population", "Total population", "total population text"),
        ("B01001e1", "B01001", "Sex By Age", "Total population", "sex by age text"),
        ("B19013e1", "B19013", "Median Household Income", "Households", "median household income text"),
        ("B19013m1", "B19013", "Median Household Income", "Households", "median household income moe text"),
        ("B99102e1", "B99102", "Allocation Of Grandparents", "Total population", "allocation grandparents text"),
    ]


def _default_geo_rows():
    """STATE is the two-letter postal abbreviation for the three real rows
    (CA/IL/WA) — verified against live Snowflake; schema-notes.md's
    "state/county FIPS -> names" description undersells that it's the
    abbreviation, not the full name. The Washington/Jefferson/Franklin
    synthetic collision rows use fake per-row STATE_FIPS/STATE identifiers
    purely to generate distinct ambiguous candidates; they're never matched
    against a real state name or abbreviation in these tests."""
    rows = []
    for i in range(30):
        rows.append((f"WashState{i:02d}", f"W{i:02d}", "Washington County", "001", "H1"))
    for i in range(25):
        rows.append((f"JeffState{i:02d}", f"J{i:02d}", "Jefferson County", "001", "H1"))
    for i in range(24):
        rows.append((f"FrankState{i:02d}", f"F{i:02d}", "Franklin County", "001", "H1"))
    rows.append(("CA", "06", "Alameda County", "001", "H1"))
    rows.append(("IL", "17", "Cook County", "031", "H1"))
    rows.append(("WA", "53", "King County", "033", "H1"))
    return rows


def _seed_snapshot(monkeypatch, variable_rows=None, geo_rows=None):
    monkeypatch.setattr(
        snapshot,
        "_connect",
        _make_fake_snowflake_connect(
            variable_rows if variable_rows is not None else _default_variable_rows(),
            geo_rows if geo_rows is not None else _default_geo_rows(),
        ),
    )
    snapshot.build_snapshot(force=True)


@pytest.fixture(autouse=True)
def isolated_snapshot_path(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_DB_PATH", tmp_path / "snapshot.sqlite3")


# --------------------------------------------------------------------------
# search_census_variables (issue #3)
# --------------------------------------------------------------------------


def test_search_returns_all_five_geo_levels_for_count_variable(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.search_census_variables("total population")
    hit = next(h for h in result.hits if h.variable_id == "B01003e1")
    assert set(hit.geo_levels) == {
        GeoLevel.NATION,
        GeoLevel.STATE,
        GeoLevel.COUNTY,
        GeoLevel.TRACT,
        GeoLevel.BLOCK_GROUP,
    }


def test_search_returns_block_group_only_for_median_variable(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.search_census_variables("median household income")
    hit = next(h for h in result.hits if h.variable_id == "B19013e1")
    assert hit.geo_levels == [GeoLevel.BLOCK_GROUP]


def test_search_ranks_more_relevant_hit_first(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.search_census_variables("total population")
    assert result.hits[0].variable_id == "B01003e1"


def test_search_score_is_higher_for_more_relevant_hit(monkeypatch):
    """CLAUDE.md rule 19 names FTS retrieval scoring a mandatory TDD target.
    contracts.py: 'score: float # FTS rank; higher is better' — SQLite's
    bm25() itself is lower-is-better, so this pins down the sign flip
    directly on the score field rather than only on hit ordering."""
    _seed_snapshot(monkeypatch)
    result = tools.search_census_variables("total population")
    assert len(result.hits) >= 2
    assert result.hits[0].score > result.hits[1].score


def test_search_sets_truncated_when_hits_exceed_limit(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.search_census_variables("population", limit=1)
    assert result.truncated is True
    assert len(result.hits) == 1


def test_search_no_matches_returns_empty_hits_not_truncated(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.search_census_variables("nonexistenttermxyz")
    assert result.hits == []
    assert result.truncated is False


def test_search_excludes_b99_and_moe_rows(monkeypatch):
    _seed_snapshot(monkeypatch)

    result = tools.search_census_variables("allocation grandparents")
    assert result.hits == []

    result = tools.search_census_variables("median household income moe")
    ids = {h.variable_id for h in result.hits}
    assert "B19013m1" not in ids
    assert "B99102e1" not in ids


def test_search_never_opens_snowflake_connection(monkeypatch):
    _seed_snapshot(monkeypatch)

    def _explode(*args, **kwargs):
        raise AssertionError("search_census_variables must not touch Snowflake")

    monkeypatch.setattr(tools, "_connect", _explode)
    tools.search_census_variables("population")


# --------------------------------------------------------------------------
# resolve_geography (issue #4)
# --------------------------------------------------------------------------


def test_washington_county_is_ambiguous_with_30_candidates(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Washington County")
    assert result.ambiguous is True
    assert len(result.candidates) == 30


def test_jefferson_county_is_ambiguous_with_25_candidates(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Jefferson County")
    assert result.ambiguous is True
    assert len(result.candidates) == 25


def test_franklin_county_is_ambiguous_with_24_candidates(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Franklin County")
    assert result.ambiguous is True
    assert len(result.candidates) == 24


def test_alameda_county_california_is_unambiguous(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Alameda County, California")
    assert result.ambiguous is False
    assert len(result.candidates) == 1
    assert result.candidates[0].geo_id == "06001"


def test_cook_county_illinois_is_unambiguous(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Cook County, Illinois")
    assert result.ambiguous is False
    assert len(result.candidates) == 1
    assert result.candidates[0].geo_id == "17031"


def test_ranking_is_deterministic_across_repeated_calls(monkeypatch):
    _seed_snapshot(monkeypatch)
    first = tools.resolve_geography("Washington County")
    second = tools.resolve_geography("Washington County")
    assert [c.geo_id for c in first.candidates] == [c.geo_id for c in second.candidates]


def test_unknown_name_returns_empty_candidates_not_ambiguous(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Zzyzxville County")
    assert result.candidates == []
    assert result.ambiguous is False


def test_level_hint_state_returns_state_not_same_named_county(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Washington", level_hint=GeoLevel.STATE)
    assert result.ambiguous is False
    assert len(result.candidates) == 1
    assert result.candidates[0].level == GeoLevel.STATE
    assert result.candidates[0].geo_id == "53"


def test_county_lookup_accepts_state_abbreviation(monkeypatch):
    """D-012: the geography table's state column stores the postal
    abbreviation. A caller passing the abbreviation directly (not just the
    full name) must resolve the same candidate — this is the exact
    normalization _normalize_state exists for."""
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Cook County, IL")
    assert result.ambiguous is False
    assert len(result.candidates) == 1
    assert result.candidates[0].geo_id == "17031"


def test_level_hint_state_accepts_abbreviation(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("WA", level_hint=GeoLevel.STATE)
    assert result.ambiguous is False
    assert len(result.candidates) == 1
    assert result.candidates[0].geo_id == "53"


def test_geo_candidate_state_is_postal_abbreviation(monkeypatch):
    _seed_snapshot(monkeypatch)
    result = tools.resolve_geography("Cook County, Illinois")
    assert result.candidates[0].state == "IL"


def test_resolve_geography_never_opens_snowflake_connection(monkeypatch):
    _seed_snapshot(monkeypatch)

    def _explode(*args, **kwargs):
        raise AssertionError("resolve_geography must not touch Snowflake")

    monkeypatch.setattr(tools, "_connect", _explode)
    tools.resolve_geography("Cook County, Illinois")


# --------------------------------------------------------------------------
# run_census_sql (issue #5)
# --------------------------------------------------------------------------


class FakeSqlCursor:
    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns]
        self._rows = rows
        self.executed_sql: list[str] = []

    def execute(self, sql):
        self.executed_sql.append(sql)

    def fetchall(self):
        return self._rows


class FakeSqlConnection:
    def __init__(self, columns, rows):
        self.cursor_obj = FakeSqlCursor(columns, rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


_VALID_SQL = 'select TABLE_ID from US_CENSUS.PUBLIC."2020_METADATA_CBG_FIELD_DESCRIPTIONS"'


def test_run_census_sql_rejects_invalid_sql_before_touching_snowflake(monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("run_census_sql must not connect for gate-rejected SQL")

    monkeypatch.setattr(tools, "_connect", _explode)

    with pytest.raises(SqlRejected):
        tools.run_census_sql('DROP TABLE US_CENSUS.PUBLIC."2020_CBG_B01"')


def test_run_census_sql_executes_valid_select_and_returns_query_result(monkeypatch):
    fake_conn = FakeSqlConnection(columns=["TABLE_ID"], rows=[("B01003e1",)])
    monkeypatch.setattr(tools, "_connect", lambda **kwargs: fake_conn)

    result = tools.run_census_sql(_VALID_SQL)

    assert isinstance(result, QueryResult)
    assert result.columns == ["TABLE_ID"]
    assert result.rows == [{"TABLE_ID": "B01003e1"}]
    assert result.row_count == 1


def test_run_census_sql_sets_statement_timeout_on_session(monkeypatch):
    fake_conn = FakeSqlConnection(columns=["TABLE_ID"], rows=[])
    captured = {}

    def _capture_connect(**kwargs):
        captured.update(kwargs)
        return fake_conn

    monkeypatch.setattr(tools, "_connect", _capture_connect)

    tools.run_census_sql(_VALID_SQL)

    assert captured["session_parameters"] == {
        "STATEMENT_TIMEOUT_IN_SECONDS": SQL_STATEMENT_TIMEOUT_S
    }


def test_run_census_sql_zero_rows_is_not_an_error(monkeypatch):
    fake_conn = FakeSqlConnection(columns=["TABLE_ID"], rows=[])
    monkeypatch.setattr(tools, "_connect", lambda **kwargs: fake_conn)

    result = tools.run_census_sql(_VALID_SQL)

    assert result.row_count == 0
    assert result.rows == []


def test_run_census_sql_executes_sanitized_sql_not_raw_input(monkeypatch):
    fake_conn = FakeSqlConnection(columns=["TABLE_ID"], rows=[])
    monkeypatch.setattr(tools, "_connect", lambda **kwargs: fake_conn)

    from src.sqlgate import validate_sql

    tools.run_census_sql(_VALID_SQL)

    expected = validate_sql(_VALID_SQL).sql
    assert fake_conn.cursor_obj.executed_sql == [expected]
    assert _VALID_SQL not in fake_conn.cursor_obj.executed_sql
