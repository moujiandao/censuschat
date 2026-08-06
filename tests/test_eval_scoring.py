"""Tests for evals/run_evals.py:_score_check.

The scorer decides whether every golden scenario passes, so a bug here
silently invalidates the whole eval artifact — it gets the same TDD
treatment as production deterministic logic (CLAUDE.md rule 19).
"""

from __future__ import annotations

from evals.run_evals import Observation, _score_check
from src.contracts import Check, CheckType


def _obs(
    *,
    answer: str = "",
    tool_calls: list | None = None,
    terminal: str = "done",
    errored: bool = False,
) -> Observation:
    obs = Observation()
    obs.final_answer = answer
    obs.tool_calls = tool_calls or []
    obs.terminal = terminal
    obs.errored = errored
    return obs


def _sql_call(ok: bool = True) -> dict:
    return {"tool": "run_census_sql", "args": '{"sql": "SELECT 1"}', "ok": ok, "summary": {}}


def test_no_unhandled_error_passes_on_clean_done():
    result = _score_check(Check(type=CheckType.NO_UNHANDLED_ERROR), _obs())
    assert result.passed is True


def test_no_unhandled_error_fails_on_error_terminal():
    result = _score_check(
        Check(type=CheckType.NO_UNHANDLED_ERROR), _obs(terminal="error", errored=True)
    )
    assert result.passed is False


def test_answer_contains_is_case_insensitive():
    check = Check(type=CheckType.ANSWER_CONTAINS, expected="Travis")
    assert _score_check(check, _obs(answer="…mostly in travis county…")).passed is True
    assert _score_check(check, _obs(answer="…in Harris County…")).passed is False


def test_variable_and_geo_resolved_search_the_tool_evidence_not_the_answer():
    """These must key off what the tools actually did, so a model that
    merely *says* a variable_id without ever resolving it fails."""
    calls = [{"tool": "resolve_geography", "args": "{}", "ok": True,
              "summary": {"resolved": ["Alameda County, California (06001)"]}}]
    geo = Check(type=CheckType.GEO_RESOLVED, expected="06001")
    assert _score_check(geo, _obs(tool_calls=calls)).passed is True
    # Same id in the prose only — no tool evidence — must NOT pass.
    assert _score_check(geo, _obs(answer="geo 06001", tool_calls=[])).passed is False


def test_expect_refusal_requires_zero_tool_calls():
    check = Check(type=CheckType.EXPECT_REFUSAL)
    assert _score_check(check, _obs(answer="I can't help")).passed is True
    assert _score_check(check, _obs(tool_calls=[_sql_call()])).passed is False


def test_expect_refusal_records_guardrail_vs_self_refusal_mechanism():
    """A canned _REFUSAL_MESSAGES string means the guardrail fired;
    anything else means the model declined on its own. Both pass, but the
    artifact must not gloss which happened."""
    from src.agent import _REFUSAL_MESSAGES

    canned = _REFUSAL_MESSAGES[None]
    assert "via guardrail" in _score_check(
        Check(type=CheckType.EXPECT_REFUSAL), _obs(answer=canned)
    ).observed
    assert "via model self-refused" in _score_check(
        Check(type=CheckType.EXPECT_REFUSAL), _obs(answer="I can't project to 2050.")
    ).observed


def test_expect_clarifying_question_requires_a_question_and_no_successful_query():
    check = Check(type=CheckType.EXPECT_CLARIFYING_QUESTION)
    assert _score_check(check, _obs(answer="Which state did you mean?")).passed is True
    # Asked a question but ALSO silently ran a query — the exact failure
    # the ambiguity policy forbids.
    assert _score_check(
        check, _obs(answer="Which one? Anyway it's 500.", tool_calls=[_sql_call(ok=True)])
    ).passed is False
    # No question at all.
    assert _score_check(check, _obs(answer="It is 500.")).passed is False


def test_judge_groundedness_fails_loudly_rather_than_silently_skipping():
    result = _score_check(Check(type=CheckType.JUDGE_GROUNDEDNESS), _obs(answer="x"))
    assert result.passed is False
    assert "not implemented" in result.observed
