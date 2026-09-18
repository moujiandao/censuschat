"""Offline reproduction and grading boundaries for the captured Texas failure."""

import asyncio
import json
from pathlib import Path

import pytest

from evals.run_evals import Observation, _score_check
from src import tools
from src.contracts import Check, CheckType, EvalOutcome, SqlRejected, SqlViolation
from src.sqlgate import validate_sql


CASE = json.loads((Path(__file__).resolve().parents[1] / "evals/cases/texas-employment-parser-rejection.json").read_text())
FIRST_SQL = Check(type=CheckType.NO_TOOL_ERRORS, expected="first_sql_validity:final_turn")
FINAL_ANSWER = Check(type=CheckType.JUDGE_GROUNDEDNESS, expected="manual_review:employment_comparison")
VALID_SQL = 'SELECT SUM("B23025e4") FROM US_CENSUS.PUBLIC."2020_CBG_B23" WHERE SUBSTR(CENSUS_BLOCK_GROUP,1,2) = \'48\''


def sql_call(sql, *, ok=True):
    return {"tool": "run_census_sql", "args": json.dumps({"sql": sql}), "ok": ok,
            "summary": {"row_count": 1} if ok else {"error": CASE["error"]}}


def observation(calls, *, answer="Texas employment comparison requires review."):
    obs = Observation()
    obs.tool_calls = calls
    obs.final_turn_tool_calls = calls
    obs.final_answer = answer
    obs.terminal = "done"
    return obs


def test_exact_production_sql_is_rejected_before_snowflake(monkeypatch):
    def forbidden_connection(*args, **kwargs):
        pytest.fail("A parser-rejected query must never connect to Snowflake")

    monkeypatch.setattr(tools, "_connect", forbidden_connection)
    result = validate_sql(CASE["generated_sql"])
    assert result.ok is False
    assert result.violations == [SqlViolation.PARSE_ERROR]
    # Preserve the exact diagnostic, including its column and ANSI highlight.
    assert result.detail == CASE["error"]
    with pytest.raises(SqlRejected) as rejected:
        tools.run_census_sql(CASE["generated_sql"])
    assert rejected.value.result == result


def test_first_sql_failure_is_not_hidden_by_successful_retry_or_prior_turn():
    obs = observation([sql_call(CASE["generated_sql"], ok=False), sql_call(VALID_SQL)])
    obs.tool_calls = [sql_call(VALID_SQL)] + obs.final_turn_tool_calls
    result = _score_check(FIRST_SQL, obs)
    assert result.outcome == EvalOutcome.FAIL
    assert "parse_error" in result.observed
    assert CASE["generated_sql"] in result.observed


def test_first_sql_validity_is_distinct_from_execution_success():
    obs = observation([sql_call(VALID_SQL, ok=False)])
    assert _score_check(FIRST_SQL, obs).outcome == EvalOutcome.PASS
    assert _score_check(FINAL_ANSWER, obs).outcome == EvalOutcome.FAIL


@pytest.mark.parametrize("calls", [[], [sql_call(CASE["retry"]["generated_sql"], ok=False)]])
def test_missing_or_unallowlisted_first_sql_cannot_pass(calls):
    assert _score_check(FIRST_SQL, observation(calls)).outcome == EvalOutcome.FAIL


@pytest.mark.parametrize("ok,outcome", [(True, EvalOutcome.PASS), (False, EvalOutcome.INCONCLUSIVE)])
def test_truncated_preview_is_not_an_invalid_query(ok, outcome):
    from src.agent import _preview

    sql = VALID_SQL + " /*" + " bounded preview " * 40 + "*/"
    assert validate_sql(sql).ok
    call = sql_call(sql, ok=ok)
    call["args"] = _preview({"sql": sql})
    assert _score_check(FIRST_SQL, observation([call])).outcome == outcome


def test_unreadable_failed_call_is_an_evidence_gap():
    call = {"tool": "run_census_sql", "args": "broken JSON", "ok": False}
    assert _score_check(FIRST_SQL, observation([call])).outcome == EvalOutcome.INCONCLUSIVE


def test_later_failure_does_not_change_first_query_validity():
    obs = observation([sql_call(VALID_SQL), sql_call(CASE["generated_sql"], ok=False)])
    assert _score_check(FIRST_SQL, obs).outcome == EvalOutcome.PASS


def test_final_correctness_cannot_pass_without_a_verified_answer_key():
    obs = observation([sql_call(CASE["generated_sql"], ok=False), sql_call(VALID_SQL)])
    result = _score_check(FINAL_ANSWER, obs)
    assert result.check == FINAL_ANSWER
    assert result.outcome == EvalOutcome.INCONCLUSIVE
    assert "manual review" in result.observed
    assert "denominator" in result.observed


@pytest.mark.parametrize("answer,terminal,errored", [("", "done", False), ("partial", "error", True)])
def test_incomplete_answer_fails_correctness_instead_of_waiting_for_review(answer, terminal, errored):
    obs = observation([sql_call(VALID_SQL)], answer=answer)
    obs.terminal, obs.errored = terminal, errored
    assert _score_check(FINAL_ANSWER, obs).outcome == EvalOutcome.FAIL


def test_live_scenario_keeps_verbatim_context_and_two_separate_checks():
    from evals.scenarios import GOLDEN_SCENARIOS

    scenario = next(s for s in GOLDEN_SCENARIOS if s.id == CASE["scenario_id"])
    assert scenario.turns == [CASE["context_question"], CASE["original_question"]]
    assert FIRST_SQL in scenario.checks
    assert FINAL_ANSWER in scenario.checks
    assert scenario.status == "pending"


def test_runner_retains_separate_results_and_default_grounding(monkeypatch):
    from evals import run_evals
    from evals.scenarios import GOLDEN_SCENARIOS

    scenario = next(s for s in GOLDEN_SCENARIOS if s.id == CASE["scenario_id"])

    async def recorded_observation(_scenario):
        return observation([sql_call(CASE["generated_sql"], ok=False), sql_call(VALID_SQL)]), 0.0

    # Exercise grading/serialization only. Never invoke the live agent.
    monkeypatch.setattr(run_evals, "_run_scenario", recorded_observation)
    result = asyncio.run(run_evals._run_all([scenario])).results[0]
    checks = {c.check.expected: c for c in result.checks if c.check.expected}
    assert checks[FIRST_SQL.expected].outcome == EvalOutcome.FAIL
    assert checks[FINAL_ANSWER.expected].outcome == EvalOutcome.INCONCLUSIVE
    assert any(c.check.type == CheckType.JUDGE_GROUNDEDNESS and c.check.expected is None
               for c in result.checks)
    assert result.outcome == EvalOutcome.FAIL
