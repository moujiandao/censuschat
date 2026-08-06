"""Tests for degraded-mode detection — src/health.py (issue #15, PRD §4.1).

Deterministic control-flow logic (snapshot file presence + a stubbed
Snowflake connect), TDD per CLAUDE.md rule 19. No live Snowflake or
filesystem dependency beyond pytest's own tmp_path.

Snowflake reachability is checked exactly once, at startup
(check_snowflake_reachability), and cached — CLAUDE.md rule 13 forbids
is_degraded()/health_report() from probing Snowflake at request time, so
several tests below assert _sf_connect is never even called from those two
functions, not just that they return the right values.
"""

from __future__ import annotations

import src.health as health


def _reset_cache(monkeypatch, reachable: bool) -> None:
    monkeypatch.setattr(health, "_snowflake_reachable_at_boot", reachable)


def test_snapshot_available_true_when_file_exists(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.sqlite3"
    snapshot_path.write_text("x")
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", snapshot_path)
    assert health.snapshot_available() is True


def test_snapshot_available_false_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", tmp_path / "missing.sqlite3")
    assert health.snapshot_available() is False


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_check_snowflake_reachability_true_when_connect_succeeds(monkeypatch):
    fake_conn = _FakeConn()
    monkeypatch.setattr(health, "_sf_connect", lambda: fake_conn)
    _reset_cache(monkeypatch, False)

    assert health.check_snowflake_reachability() is True
    assert fake_conn.closed is True
    assert health._snowflake_reachable_at_boot is True


def test_check_snowflake_reachability_false_when_connect_raises(monkeypatch):
    def _raise():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health, "_sf_connect", _raise)
    _reset_cache(monkeypatch, True)

    assert health.check_snowflake_reachability() is False
    assert health._snowflake_reachable_at_boot is False


def test_check_snowflake_reachability_survives_close_raising(monkeypatch):
    """A connection that succeeds but errors on close() must still count
    as reachable — the probe already got a live session, which is all
    "reachable" means; a close-time error is a cleanup detail, not a
    reachability signal."""

    class _RaisingCloseConn:
        def close(self):
            raise RuntimeError("close failed")

    monkeypatch.setattr(health, "_sf_connect", lambda: _RaisingCloseConn())
    _reset_cache(monkeypatch, False)

    assert health.check_snowflake_reachability() is True


def test_is_degraded_reads_cached_state_never_probes_live(monkeypatch, tmp_path):
    """CLAUDE.md rule 13: Snowflake is touched at request time solely by
    run_census_sql. is_degraded() runs on the chat hot path, so it must
    read the boot-time cache only — proven here by making _sf_connect raise
    AssertionError if it's ever called."""
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", tmp_path / "missing.sqlite3")
    _reset_cache(monkeypatch, False)

    def _should_not_be_called():
        raise AssertionError("is_degraded must never touch Snowflake at request time")

    monkeypatch.setattr(health, "_sf_connect", _should_not_be_called)

    assert health.is_degraded() is True


def test_is_degraded_false_when_snapshot_present_even_if_snowflake_cached_unreachable(
    monkeypatch, tmp_path
):
    """PRD §4.1: a snapshot-present, Snowflake-asleep cold boot must not be
    treated as degraded — search/geography tools still work fully off the
    local snapshot regardless of Snowflake state."""
    snapshot_path = tmp_path / "snapshot.sqlite3"
    snapshot_path.write_text("x")
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", snapshot_path)
    _reset_cache(monkeypatch, False)

    assert health.is_degraded() is False


def test_is_degraded_false_when_snowflake_cached_reachable_even_if_snapshot_missing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", tmp_path / "missing.sqlite3")
    _reset_cache(monkeypatch, True)
    assert health.is_degraded() is False


def test_health_report_reads_cached_state_never_probes_live(monkeypatch, tmp_path):
    """Same rule-13 guarantee as is_degraded, for /api/health."""
    snapshot_path = tmp_path / "snapshot.sqlite3"
    snapshot_path.write_text("x")
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", snapshot_path)
    _reset_cache(monkeypatch, True)

    def _should_not_be_called():
        raise AssertionError("health_report must never touch Snowflake at request time")

    monkeypatch.setattr(health, "_sf_connect", _should_not_be_called)

    assert health.health_report() == {"status": "ok", "snapshot": "ok", "snowflake": "ok"}


def test_health_report_degraded_when_both_unhealthy(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", tmp_path / "missing.sqlite3")
    _reset_cache(monkeypatch, False)

    assert health.health_report() == {
        "status": "degraded",
        "snapshot": "missing",
        "snowflake": "unreachable",
    }


def test_health_report_distinguishes_causes_when_only_snowflake_unreachable(monkeypatch, tmp_path):
    """Exit criterion 5: distinct degraded causes must be distinguishable
    even when overall status is 'ok' — a snapshot-present, Snowflake-down
    state is a real signal an operator should see, without alone flipping
    the app into degraded mode."""
    snapshot_path = tmp_path / "snapshot.sqlite3"
    snapshot_path.write_text("x")
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", snapshot_path)
    _reset_cache(monkeypatch, False)

    assert health.health_report() == {"status": "ok", "snapshot": "ok", "snowflake": "unreachable"}


def test_health_report_distinguishes_causes_when_only_snapshot_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "SNAPSHOT_DB_PATH", tmp_path / "missing.sqlite3")
    _reset_cache(monkeypatch, True)

    assert health.health_report() == {"status": "ok", "snapshot": "missing", "snowflake": "ok"}
