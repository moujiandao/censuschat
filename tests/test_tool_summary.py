"""Tests for src/agent.py:_summarize_tool_result — the per-tool detail
carried on TOOL_END events for the Turn Detail / Trace Logging tabs.

Pure function, deterministic — TDD per CLAUDE.md rule 19. The security-
relevant case is the error branch: this summary is client-facing, so it
must use the already-sanitized error track (src/app.py:28-30's "user-safe
text only" convention, the same one issue #12's review enforced on
recovery messages), never a raw driver exception.
"""

from __future__ import annotations

from src.agent import _summarize_tool_result


def test_search_summary_reports_hit_count_and_top_variable_ids():
    payload = {
        "query": "total population",
        "hits": [
            {"variable_id": f"B0100{i}e1", "label": f"Label {i}", "score": 1.0}
            for i in range(8)
        ],
        "truncated": False,
    }
    summary = _summarize_tool_result("search_census_variables", payload, False, None)
    assert summary["hits"] == 8
    # Bounded — never dumps all hits into a client-facing payload.
    assert len(summary["top"]) == 5
    assert summary["top"][0] == "B01000e1"


def test_search_summary_handles_zero_hits():
    summary = _summarize_tool_result(
        "search_census_variables", {"query": "nonsense", "hits": []}, False, None
    )
    assert summary["hits"] == 0
    assert summary["top"] == []


def test_geography_summary_reports_candidates_and_ambiguity():
    payload = {
        "query": "Washington County",
        "candidates": [
            {"geo_id": "24043", "name": "Washington County, Maryland", "level": "county", "state": "MD"},
            {"geo_id": "51191", "name": "Washington County, Virginia", "level": "county", "state": "VA"},
        ],
        "ambiguous": True,
    }
    summary = _summarize_tool_result("resolve_geography", payload, False, None)
    assert summary["candidates"] == 2
    assert summary["ambiguous"] is True
    assert "Washington County, Maryland (24043)" in summary["resolved"]


def test_geography_summary_bounds_a_large_candidate_list():
    """"Washington County" really does match 30 states — the summary must
    stay bounded rather than dumping every candidate to the client."""
    payload = {
        "query": "Washington County",
        "candidates": [
            {"geo_id": f"{i:05d}", "name": f"Washington County, State {i}", "level": "county"}
            for i in range(30)
        ],
        "ambiguous": True,
    }
    summary = _summarize_tool_result("resolve_geography", payload, False, None)
    assert summary["candidates"] == 30
    assert len(summary["resolved"]) == 5


def test_sql_summary_reports_rows_columns_and_a_single_row_preview():
    payload = {
        "columns": ["TOTAL_POPULATION"],
        "rows": [{"TOTAL_POPULATION": 581348}],
        "row_count": 1,
        "truncated": False,
        "elapsed_ms": 4614,
    }
    summary = _summarize_tool_result("run_census_sql", payload, False, None)
    assert summary["row_count"] == 1
    assert summary["columns"] == ["TOTAL_POPULATION"]
    assert summary["first_row"] == {"TOTAL_POPULATION": 581348}
    assert summary["truncated"] is False


def test_sql_summary_previews_only_the_first_row_of_many():
    """A query can return up to SQL_ROW_LIMIT rows; the client-facing
    summary shows exactly one so the payload stays bounded regardless."""
    payload = {
        "columns": ["GEO", "POP"],
        "rows": [{"GEO": f"{i}", "POP": i * 100} for i in range(200)],
        "row_count": 200,
        "truncated": True,
        "elapsed_ms": 900,
    }
    summary = _summarize_tool_result("run_census_sql", payload, False, None)
    assert summary["row_count"] == 200
    assert summary["first_row"] == {"GEO": "0", "POP": 0}
    assert summary["truncated"] is True


def test_sql_summary_handles_zero_rows():
    payload = {"columns": ["POP"], "rows": [], "row_count": 0, "truncated": False, "elapsed_ms": 12}
    summary = _summarize_tool_result("run_census_sql", payload, False, None)
    assert summary["row_count"] == 0
    assert summary["first_row"] is None


def test_error_summary_uses_the_sanitized_detail_never_the_raw_payload():
    """The security-relevant case. `result_payload["error"]` carries the
    RAW exception for the model's benefit (it needs real detail to
    self-correct); the client-facing summary must use the sanitized
    `error_detail` track instead — same two-track split agent_turn already
    maintains for recovery messages."""
    raw = "run_census_sql failed: ProgrammingError at internal-sf-01.snowflakecomputing.com"
    summary = _summarize_tool_result(
        "run_census_sql",
        {"error": raw},
        True,
        "run_census_sql raised an internal error",
    )
    assert summary["error"] == "run_census_sql raised an internal error"
    assert "snowflakecomputing.com" not in str(summary)


def test_error_summary_keeps_gate_rejection_detail_which_is_safe_vocabulary():
    """A SqlRejected message is our own gate's controlled vocabulary
    (validate_sql violations), not driver text — it's passed through as the
    sanitized detail by agent_turn, so it stays useful here."""
    detail = "table(s) not in the allowlist: INFORMATION_SCHEMA.COLUMNS"
    summary = _summarize_tool_result("run_census_sql", {"error": detail}, True, detail)
    assert summary["error"] == detail


def test_unknown_tool_returns_an_empty_summary_rather_than_raising():
    assert _summarize_tool_result("some_future_tool", {"anything": 1}, False, None) == {}


def test_malformed_payload_never_raises():
    """Defensive: a tool returning an unexpected shape must degrade to a
    partial summary, never break the turn it's describing."""
    assert _summarize_tool_result("search_census_variables", {}, False, None)["hits"] == 0
    assert _summarize_tool_result("resolve_geography", {}, False, None)["candidates"] == 0
    assert _summarize_tool_result("run_census_sql", {}, False, None)["row_count"] == 0
