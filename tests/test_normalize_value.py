"""Tests for src/contracts.py:normalize_value (issue #16, scoped down).

Pure function, deterministic — TDD per CLAUDE.md rule 19. Two documented
cases only: SQL NULL -> suppressed (never coerced to 0), and a raw value
matching TOP_CODES for its variable's table -> top_coded (the caller
renders the band, never the bare figure).
"""

from __future__ import annotations

from src.contracts import CensusValue, normalize_value


def test_null_raw_is_suppressed_not_coerced_to_zero():
    result = normalize_value(None, "B01001e23")
    assert result.value is None
    assert result.suppressed is True
    assert result.raw is None


def test_ordinary_value_passes_through_unsuppressed_untopcoded():
    result = normalize_value(1250884, "B01001e23")
    assert result.value == 1250884.0
    assert result.suppressed is False
    assert result.top_coded is False


def test_top_coded_value_flagged_for_matching_table_and_value():
    """B19013 (median household income) top-codes at 250001.0 — "$250,000
    or more," a real value carrying special meaning, not a number to
    render as-is."""
    result = normalize_value(250001.0, "B19013e1")
    assert result.value == 250001.0
    assert result.top_coded is True
    assert result.suppressed is False


def test_value_matching_top_code_number_but_different_table_not_flagged():
    """The top-code threshold is table-specific — 250001 in an unrelated
    table (not in TOP_CODES) is just an ordinary value."""
    result = normalize_value(250001.0, "B01001e23")
    assert result.top_coded is False


def test_value_in_top_coded_table_but_below_threshold_not_flagged():
    """Only the exact top-code value is a top-code; a lower income in the
    same table is a real, ordinary estimate."""
    result = normalize_value(85000.0, "B19013e1")
    assert result.top_coded is False


def test_no_variable_id_never_top_codes():
    """Without a variable_id there's no table to check against
    TOP_CODES — never guess."""
    result = normalize_value(250001.0, None)
    assert result.top_coded is False


def test_returns_census_value_instance():
    assert isinstance(normalize_value(42, "B01001e23"), CensusValue)
