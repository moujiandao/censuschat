"""Repo-root conftest.

Its first job is to exist: pytest's default `prepend` import mode inserts the
rootdir (the directory holding this file) at the front of ``sys.path``, so
tests can ``from src.sqlgate import validate_sql`` without a packaging step.
``src/`` is a PEP 420 namespace package — no ``__init__.py`` required.

Its second job is to keep the suite off the real on-disk stores.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_trace_store(tmp_path, monkeypatch):
    """Point the trace store at a throwaway file for every test.

    Traces became durable in D-023. Before that they lived in a process dict,
    so a test exercising `agent_turn` recorded a trace that died with the
    process and bothered nobody. The moment that dict became a SQLite file on
    the default path, the same tests began writing into `data/traces.sqlite3`
    — the store the running app reads — and a reviewer opening Evidence would
    find `s-watchdog` and `s-refuse` sitting in their history.

    Autouse and repo-wide on purpose. Any test that reaches `agent_turn` gets
    tracing whether or not it is about tracing, so opting in per test is the
    wrong default: the one test that forgets is the one that pollutes.
    """
    import src.tracing as tracing

    monkeypatch.setattr(tracing, "TRACE_DB_PATH", tmp_path / "traces.sqlite3")
