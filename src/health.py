"""Degraded-mode detection — SnapshotError's own docstring
(src/contracts.py), PRD §4.1/§5/§12, issue #15.

Snapshot presence and Snowflake reachability are checked independently;
the app (and a chat turn) is only degraded when BOTH fail. Per PRD §4.1, a
merely-asleep warehouse must never flip the app into degraded mode on
every cold boot — Snowflake auto-resumes a suspended warehouse on query
execution, so a bare connection (no query run) succeeds regardless of
warehouse state, which is what "reachable" means here.

Snowflake reachability is checked exactly once, at app startup —
`check_snowflake_reachability()` is called only from src/app.py's lifespan
handler — and cached in this module. Per CLAUDE.md rule 13 ("At request
time, Snowflake is touched solely by run_census_sql"), neither
`is_degraded()` (the chat hot path) nor `health_report()` (/api/health)
may probe Snowflake live; both read the cached boot-time result. This
means the cached value can go stale if Snowflake changes state mid-run
without an app restart — accepted per Brian's direction: /api/health has
no continuous production consumer (deploy.sh only polls it right after a
fresh boot), and a genuine mid-session Snowflake outage is already
surfaced honestly the next time run_census_sql actually fails, via bounded
recovery (issue #12, MAX_RECOVERY_RETRIES) — so a stale cached "ok"
between boots is not a silent failure mode.
"""

from __future__ import annotations

from src.snapshot import SNAPSHOT_DB_PATH
from src.snowflake_conn import connect as _sf_connect

_snowflake_reachable_at_boot: bool = False


def snapshot_available() -> bool:
    return SNAPSHOT_DB_PATH.exists()


def check_snowflake_reachability() -> bool:
    """The only live Snowflake probe in this module — call once, from the
    startup lifespan handler, never at request time. Opens and immediately
    closes a session; never runs a query, so this can't itself trigger a
    warehouse resume or be fooled into reporting "unreachable" just
    because the warehouse happens to be suspended. Bounded by
    SNOWFLAKE_CONNECT_TIMEOUT_S (applied inside snowflake_conn.connect's
    login_timeout) so a hanging connection can't hang startup — issue
    #15's own "the app must still boot" requirement."""
    global _snowflake_reachable_at_boot
    try:
        conn = _sf_connect()
    except Exception:
        _snowflake_reachable_at_boot = False
        return False
    try:
        conn.close()
    except Exception:
        pass
    _snowflake_reachable_at_boot = True
    return True


def is_degraded() -> bool:
    """The chat hot-path check. No Snowflake I/O at request time (rule
    13) — reads the boot-time cached result only. snapshot_available() is
    a local file stat, not Snowflake, so it's still checked live; the
    `and` still short-circuits it against the cheap local check first."""
    return not snapshot_available() and not _snowflake_reachable_at_boot


def health_report() -> dict:
    """/api/health. Reads the same boot-time cached Snowflake result as
    is_degraded(), alongside a live snapshot_available() check, so the two
    causes stay distinguishable in the payload even when overall status is
    "ok" — e.g. snapshot present, Snowflake unreachable at boot is a real
    signal worth surfacing without alone flipping the app degraded."""
    snapshot_ok = snapshot_available()
    snowflake_ok = _snowflake_reachable_at_boot
    return {
        "status": "degraded" if (not snapshot_ok and not snowflake_ok) else "ok",
        "snapshot": "ok" if snapshot_ok else "missing",
        "snowflake": "ok" if snowflake_ok else "unreachable",
    }
