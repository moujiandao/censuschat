"""Golden-set eval harness (issue #19, partial).

Drives every scenario in evals/scenarios.py against the REAL agent_turn —
real Anthropic, real Snowflake, real guardrail — collects the ChatEvent
stream, scores the deterministic checks, and writes an EvalRun
(src/contracts.py) to evals/results/<timestamp>.json + latest.json.

Run with `make eval` (or `python -m evals.run_evals`). Requires .env
credentials; this is a live-call harness by design, since the whole point
is testing generative behavior that mocked unit tests can't reach
(CLAUDE.md rule 19).

Red rows are kept and reported, never dropped to make a run look clean
(CLAUDE.md rule 20).

Not implemented: JUDGE_GROUNDEDNESS (issue #21) — the only LLM-judge
check. No scenario currently carries it; if one did, it would be scored
as an explicit "not implemented" failure rather than silently skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.agent import _REFUSAL_MESSAGES, agent_turn
from src.contracts import (
    Check,
    CheckResult,
    CheckType,
    EvalResult,
    EvalRun,
    EvalScenario,
    EventType,
)

RESULTS_DIR = Path(__file__).parent / "results"

# The exact canned strings agent_turn emits on a guardrail REFUSE verdict —
# used to tell a guardrail refusal apart from a model self-refusal.
_CANNED_REFUSALS = set(_REFUSAL_MESSAGES.values())


class Observation:
    """What one scenario's turns actually produced.

    Two scopes, deliberately different, because different checks need
    different ones:

    - `tool_calls` accumulates across EVERY turn. GEO_RESOLVED and
      VARIABLE_RESOLVED need this — MT-01 resolves its geography on turn 1
      and is judged as a whole.
    - `final_turn_tool_calls` covers only the LAST turn. EXPECT_REFUSAL
      needs this: on a drift scenario like OT-04, turns 1-2 legitimately
      call tools and only turn 3 must refuse. Judging that against the
      accumulated list would fail every multi-turn refusal by construction.
    """

    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.final_turn_tool_calls: list[dict] = []
        self.final_answer: str = ""
        self.terminal: str | None = None
        self.errored: bool = False

    def start_turn(self) -> None:
        """Called before each turn — resets the per-turn view while leaving
        the accumulated one intact."""
        self.final_turn_tool_calls = []

    def record_tool_call(self, call: dict) -> None:
        self.tool_calls.append(call)
        self.final_turn_tool_calls.append(call)

    @property
    def tool_evidence(self) -> str:
        """Everything the tools were asked and returned, as one searchable
        blob — this is what VARIABLE_RESOLVED / GEO_RESOLVED look in."""
        return json.dumps(self.tool_calls)

    @property
    def ran_sql_successfully(self) -> bool:
        return any(
            c["tool"] == "run_census_sql" and c["ok"] for c in self.tool_calls
        )


async def _run_scenario(scenario: EvalScenario) -> tuple[Observation, float]:
    """Drives all of a scenario's turns sequentially in ONE session, so
    multi-turn context replay is exercised for real."""
    session_id = f"eval-{scenario.id}-{uuid.uuid4().hex[:8]}"
    obs = Observation()
    start = time.monotonic()

    for turn in scenario.turns:
        text_parts: list[str] = []
        pending_tool: dict | None = None
        obs.start_turn()
        async for event in agent_turn(session_id, turn):
            if event.type == EventType.TOKEN:
                text_parts.append(event.data.get("text", ""))
            elif event.type == EventType.TOOL_START:
                pending_tool = {
                    "tool": event.data.get("tool"),
                    "args": event.data.get("args_preview", ""),
                }
            elif event.type == EventType.TOOL_END:
                call = pending_tool or {"tool": event.data.get("tool"), "args": ""}
                call["ok"] = bool(event.data.get("ok"))
                call["summary"] = event.data.get("summary", {})
                obs.record_tool_call(call)
                pending_tool = None
            elif event.type in (EventType.DONE, EventType.ERROR):
                obs.terminal = event.type.value
                if event.type == EventType.ERROR:
                    obs.errored = True
        # Each turn overwrites; the LAST turn's answer is the one judged.
        obs.final_answer = "".join(text_parts).strip()

    return obs, time.monotonic() - start


def _score_check(check: Check, obs: Observation) -> CheckResult:
    """Every check here is deterministic. Where a check is an
    operationalization of a fuzzier intent, the definition is stated
    explicitly rather than left implicit."""
    expected = check.expected or ""

    if check.type == CheckType.NO_UNHANDLED_ERROR:
        passed = obs.terminal == "done" and not obs.errored
        return CheckResult(check=check, passed=passed, observed=f"terminal={obs.terminal}")

    if check.type == CheckType.ANSWER_CONTAINS:
        passed = expected.lower() in obs.final_answer.lower()
        return CheckResult(check=check, passed=passed, observed=obs.final_answer[:200])

    if check.type == CheckType.VARIABLE_RESOLVED:
        passed = expected in obs.tool_evidence
        return CheckResult(check=check, passed=passed, observed=obs.tool_evidence[:200])

    if check.type == CheckType.GEO_RESOLVED:
        passed = expected in obs.tool_evidence
        return CheckResult(check=check, passed=passed, observed=obs.tool_evidence[:200])

    if check.type == CheckType.EXPECT_REFUSAL:
        # Operationalized as: nothing ran — no tool call, so Snowflake was
        # never touched and the turn ended cleanly. That is the behavioral
        # property the PRD cares about.
        #
        # Note it is NOT the same as CheckType's own wording ("guardrail
        # fired"): a turn can also reach zero tool calls because the
        # guardrail ALLOWED it and Sonnet then declined on its own. Both are
        # correct outcomes, but they're different mechanisms, so the
        # mechanism is recorded rather than glossed — a canned message from
        # _REFUSAL_MESSAGES means the guardrail fired; anything else means
        # the model self-refused.
        # Scoped to the FINAL turn, not the whole scenario: a drift case
        # (OT-04) has two legitimate tool-using turns before the one that
        # must refuse, and judging the accumulated list would fail it by
        # construction rather than on merit.
        passed = len(obs.final_turn_tool_calls) == 0 and obs.terminal == "done"
        mechanism = (
            "guardrail" if obs.final_answer in _CANNED_REFUSALS else "model self-refused"
        )
        return CheckResult(
            check=check,
            passed=passed,
            observed=(
                f"{len(obs.final_turn_tool_calls)} tool calls on final turn "
                f"({len(obs.tool_calls)} across scenario), via {mechanism}; "
                f"{obs.final_answer[:100]}"
            ),
        )

    if check.type == CheckType.EXPECT_CLARIFYING_QUESTION:
        # Operationalized as: the turn ends by asking something, and never
        # produced a successful query — i.e. it did NOT silently pick one
        # of the ambiguous candidates and answer with a number.
        passed = "?" in obs.final_answer and not obs.ran_sql_successfully
        return CheckResult(
            check=check,
            passed=passed,
            observed=f"ran_sql={obs.ran_sql_successfully}; {obs.final_answer[:120]}",
        )

    if check.type == CheckType.JUDGE_GROUNDEDNESS:
        return CheckResult(
            check=check,
            passed=False,
            observed="judge_groundedness not implemented (issue #21, cut)",
        )

    return CheckResult(check=check, passed=False, observed=f"unknown check {check.type}")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _select(scenario_ids: list[str]) -> list[str]:
    """Resolve --only ids, failing loudly on a typo. Silently running 4 of the
    5 ids you asked for is worse than not running at all: the missing one looks
    like it was checked."""
    from evals.scenarios import GOLDEN_SCENARIOS

    known = {s.id for s in GOLDEN_SCENARIOS}
    unknown = [i for i in scenario_ids if i not in known]
    if unknown:
        raise SystemExit(f"--only: unknown scenario id(s) {unknown}; known: {sorted(known)}")
    return scenario_ids


async def main() -> int:
    from evals.scenarios import GOLDEN_SCENARIOS

    parser = argparse.ArgumentParser(prog="python -m evals.run_evals")
    parser.add_argument(
        "--only",
        default="",
        help=(
            "Comma-separated scenario ids to run instead of the full executed "
            "set. A filtered run writes NOTHING to evals/results/ — see below."
        ),
    )
    args = parser.parse_args()
    only = _select([i.strip() for i in args.only.split(",") if i.strip()])

    executed = [s for s in GOLDEN_SCENARIOS if s.status == "executed"]
    pending = [s for s in GOLDEN_SCENARIOS if s.status == "pending"]
    if only:
        # A filtered run is a debugging aid, not a result. It deliberately
        # ignores `status` so a pending row can be tried without promoting it.
        executed = [s for s in GOLDEN_SCENARIOS if s.id in only]
        pending = []

    results: list[EvalResult] = []
    for scenario in executed:
        print(f"running {scenario.id} ({scenario.category.value})…", flush=True)
        try:
            obs, elapsed = await _run_scenario(scenario)
        except Exception as exc:  # noqa: BLE001 — a crashed scenario is a red row, not a crashed run
            print(f"  !! {scenario.id} raised: {exc}", flush=True)
            results.append(
                EvalResult(
                    scenario_id=scenario.id,
                    category=scenario.category,
                    passed=False,
                    checks=[
                        CheckResult(check=c, passed=False, observed=f"scenario raised: {exc}")
                        for c in scenario.checks
                    ],
                    answer_final="",
                    elapsed_s=0.0,
                )
            )
            continue

        check_results = [_score_check(c, obs) for c in scenario.checks]
        passed = all(c.passed for c in check_results)
        results.append(
            EvalResult(
                scenario_id=scenario.id,
                category=scenario.category,
                passed=passed,
                checks=check_results,
                answer_final=obs.final_answer,
                elapsed_s=round(elapsed, 1),
            )
        )
        mark = "PASS" if passed else "FAIL"
        print(f"  {mark} ({elapsed:.1f}s)", flush=True)

    # Pass rate and by-category are computed from EXECUTED rows only, before
    # pending rows are appended. An unrun backlog must never move a real
    # number in either direction — 25 pending scenarios dragging a genuine
    # 11/11 down to 11/36 would be as dishonest as hiding them entirely.
    by_category: dict[str, list[bool]] = {}
    for r in results:
        by_category.setdefault(r.category.value, []).append(r.passed)

    n_executed = len(results)
    n_passed = sum(1 for r in results if r.passed)
    pass_rate = n_passed / n_executed if n_executed else 0.0

    # Appended AFTER the denominators are fixed, purely so the Evals tab can
    # show the backlog. passed=False here means "no evidence", not "failed";
    # the status field is what distinguishes them and the UI renders on that.
    results = results + [
        EvalResult(
            scenario_id=s.id,
            category=s.category,
            passed=False,
            checks=[],
            answer_final="",
            elapsed_s=0.0,
            status="pending",
        )
        for s in pending
    ]

    run = EvalRun(
        run_at=datetime.now(timezone.utc),
        git_sha=_git_sha(),
        results=results,
        pass_rate=pass_rate,
        by_category={k: sum(v) / len(v) for k, v in by_category.items()},
    )

    print(f"\n{n_passed}/{n_executed} executed passed ({pass_rate:.0%})"
          f" — {len(pending)} pending, excluded from the denominator")
    for r in results:
        if r.status == "executed" and not r.passed:
            failed = [c.check.type.value for c in r.checks if not c.passed]
            print(f"  RED  {r.scenario_id:8s} {', '.join(failed)}")

    # A filtered run must never touch evals/results/. Its pass rate is over a
    # hand-picked subset, so writing it would overwrite the committed artifact
    # with a number that looks like a full run and isn't. Rule 20's "red rows
    # are kept" only means anything if latest.json is always a whole run.
    if only:
        print("\n--only: filtered run, nothing written to evals/results/")
        return 0

    RESULTS_DIR.mkdir(exist_ok=True)
    payload = json.loads(run.model_dump_json())
    stamp = run.run_at.strftime("%Y%m%dT%H%M%SZ")
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(payload, indent=2))
    (RESULTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2))
    print(f"wrote evals/results/{stamp}.json and latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
