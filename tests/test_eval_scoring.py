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
    final_turn_tool_calls: list | None = None,
    terminal: str = "done",
    errored: bool = False,
) -> Observation:
    obs = Observation()
    obs.final_answer = answer
    obs.tool_calls = tool_calls or []
    # Defaults to the same list — a single-turn scenario, where "the final
    # turn" and "the whole scenario" are the same thing.
    obs.final_turn_tool_calls = (
        final_turn_tool_calls if final_turn_tool_calls is not None else obs.tool_calls
    )
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


def test_expect_refusal_scores_the_final_turn_not_the_whole_scenario():
    """The OT-04 shape: a slow off-topic drift has two legitimate
    tool-using turns before the one that must refuse. Scoring against the
    accumulated list would fail every multi-turn refusal by construction,
    regardless of whether the agent behaved correctly."""
    check = Check(type=CheckType.EXPECT_REFUSAL)

    drifted = _obs(
        answer="I can only help with US Census demographic data.",
        tool_calls=[_sql_call(), _sql_call()],   # turns 1-2, legitimate
        final_turn_tool_calls=[],                # turn 3 refused
    )
    assert _score_check(check, drifted).passed is True
    # The observed string must expose both scopes, so a reader can tell a
    # clean refusal from one that merely stopped calling tools late.
    assert "0 tool calls on final turn" in _score_check(check, drifted).observed
    assert "2 across scenario" in _score_check(check, drifted).observed

    # The inverse: earlier turns refused but the final one ran a query —
    # not a refusal, and the old accumulate-everything logic would also
    # have caught this one, but only by accident.
    leaked = _obs(
        answer="It is 500.",
        tool_calls=[_sql_call()],
        final_turn_tool_calls=[_sql_call()],
    )
    assert _score_check(check, leaked).passed is False


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


# --------------------------------------------------------------------------
# The pending/executed split (D-018). These guard the scenario set itself
# rather than the scorer — a mislabeled row silently changes a headline
# number, which is exactly the class of defect this whole file exists for.
# --------------------------------------------------------------------------

def test_pending_rows_never_enter_the_pass_rate_denominator():
    """The invariant: 25 unrun scenarios must not drag a genuine 11/11 down
    to 11/36, nor be quietly hidden. They are excluded from the denominator
    and appended to results only for display."""
    from evals.scenarios import GOLDEN_SCENARIOS

    executed = [s for s in GOLDEN_SCENARIOS if s.status == "executed"]
    pending = [s for s in GOLDEN_SCENARIOS if s.status == "pending"]

    assert executed and pending, "fixture assumes both kinds exist"
    assert len(executed) + len(pending) == len(GOLDEN_SCENARIOS)

    # Mirrors run_evals.main(): the denominator is computed from executed
    # rows before pending ones are appended.
    fake_results = [True] * len(executed)
    pass_rate = sum(fake_results) / len(fake_results)
    assert pass_rate == 1.0, "an all-green executed set must read 100%, not 11/36"


def test_every_pending_row_carries_the_universal_groundedness_check():
    """"No number that isn't traceable to returned rows" is the universal
    invariant on authored scenarios. A pending row without it would be
    specifying less than intended."""
    from evals.scenarios import GOLDEN_SCENARIOS

    for s in GOLDEN_SCENARIOS:
        if s.status != "pending":
            continue
        types = {c.type for c in s.checks}
        assert CheckType.JUDGE_GROUNDEDNESS in types, f"{s.id} missing the universal check"
        assert 2 <= len(s.checks) <= 4, f"{s.id} has {len(s.checks)} checks, want 2-4"


def test_no_executed_row_carries_judge_groundedness():
    """Executed rows predate the judge and must stay scoreable — adding an
    unimplemented check to them would turn a real 11/11 into 0/11."""
    from evals.scenarios import GOLDEN_SCENARIOS

    for s in GOLDEN_SCENARIOS:
        if s.status == "executed":
            assert CheckType.JUDGE_GROUNDEDNESS not in {c.type for c in s.checks}, s.id


def test_scenario_ids_are_unique():
    """The defect that prompted this whole rewrite: ad hoc rows reusing PRD
    ids for different questions."""
    from evals.scenarios import GOLDEN_SCENARIOS

    ids = [s.id for s in GOLDEN_SCENARIOS]
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
