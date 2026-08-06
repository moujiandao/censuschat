"""The generated id-reference blocks must stay in sync with reality.

A glossary that quietly goes stale is worse than none: it states a question
the scenario no longer asks, and a reader has no reason to doubt it. This
pins that every id mentioned in a doc resolves, and that the committed blocks
match what the generator would produce today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_id_reference import (
    DECISION_RE,
    SCENARIO_RE,
    TARGETS,
    _decisions,
    _live_scenarios,
    _prd_scenarios,
    _retired_scenarios,
    main,
)

ROOT = Path(__file__).resolve().parent.parent


def test_every_scenario_id_used_in_a_doc_resolves_to_something():
    """An id with no definition renders as "no definition found", which is an
    honest placeholder and not something to ship."""
    known = set(_live_scenarios()) | set(_prd_scenarios()) | set(_retired_scenarios())
    unknown: dict[str, set[str]] = {}
    for rel, want_scen, _ in TARGETS:
        if not want_scen:
            continue
        body = (ROOT / rel).read_text().split("<!-- BEGIN id-reference")[0]
        missing = set(SCENARIO_RE.findall(body)) - known
        if missing:
            unknown[rel] = missing
    assert not unknown, f"scenario ids with no definition: {unknown}"


def test_every_decision_id_used_in_a_doc_resolves_to_a_heading():
    known = set(_decisions())
    unknown: dict[str, set[str]] = {}
    for rel, _, want_dec in TARGETS:
        if not want_dec:
            continue
        body = (ROOT / rel).read_text().split("<!-- BEGIN id-reference")[0]
        missing = set(DECISION_RE.findall(body)) - known
        if missing:
            unknown[rel] = missing
    assert not unknown, f"decision ids with no docs/decisions.md heading: {unknown}"


def test_live_scenario_questions_are_quoted_verbatim():
    """The whole point is that the question shown is the one that runs, so it
    must come from the scenario objects rather than being retyped."""
    from evals.scenarios import GOLDEN_SCENARIOS

    live = _live_scenarios()
    for s in GOLDEN_SCENARIOS:
        for turn in s.turns:
            assert turn in live[s.id], f"{s.id} turn text not carried through"


def test_committed_blocks_are_not_stale(capsys):
    """Fails when a scenario or decision changed and the docs were not
    regenerated. Fix: python -m scripts.build_id_reference"""
    assert main.__call__ is not None
    import sys

    argv = sys.argv
    sys.argv = ["build_id_reference", "--check"]
    try:
        code = main()
    finally:
        sys.argv = argv
    if code != 0:
        pytest.fail(capsys.readouterr().out.strip())
