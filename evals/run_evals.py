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

`JUDGE_GROUNDEDNESS` retains its legacy enum name, but is implemented as a
deterministic numeric evidence check. It is appended to every scenario and
may return an inconclusive outcome when bounded tool evidence hides later rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

# MUST run before importing src.agent: that module builds its Anthropic client
# at import time, reading the key straight from the process environment. Import
# it first and the client is constructed keyless, and every scenario then dies
# on "Could not resolve authentication method" — which the harness dutifully
# scores as 14 genuine failures. The docstring above and the Makefile both
# promised .env support that nothing actually implemented.
load_dotenv()

from src.agent import _REFUSAL_MESSAGES, agent_turn  # noqa: E402
from src.contracts import (  # noqa: E402
    Check,
    CheckResult,
    CheckType,
    EvalOutcome,
    EvalResult,
    EvalRun,
    EvalScenario,
    EventType,
)

RESULTS_DIR = Path(__file__).parent / "results"

# Checked before anything runs. A missing credential is not a red row: it means
# nothing was measured, and writing "0/14 passed" for it produces an artifact
# that looks like a catastrophic regression and is really a config error. The
# committed history is what the Evals tab draws its trend from, so a fabricated
# 0% column is worse than a crash.
_REQUIRED_ENV = (
    "ANTHROPIC_API_KEY",
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_ROLE",
)


def _require_credentials() -> None:
    missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise SystemExit(
            "missing credentials: " + ", ".join(missing) + "\n"
            "This is a live-call harness. Populate .env (see .env.example) or "
            "export them.\nNothing was run and nothing was written to "
            "evals/results/."
        )

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
    def returned_values(self) -> list[float]:
        """Every numeric value this turn's successful run_census_sql calls
        actually returned. This is the literal evidence set CLAUDE.md rule 2
        names: "rows returned by this turn's QueryResults"."""
        values: list[float] = []
        for call in self.tool_calls:
            if call["tool"] != "run_census_sql" or not call["ok"]:
                continue
            row = (call.get("summary") or {}).get("first_row") or {}
            for v in row.values():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    values.append(float(v))
                elif isinstance(v, str):
                    try:
                        values.append(float(v.replace(",", "")))
                    except ValueError:
                        pass
        return values

    @property
    def has_unseen_rows(self) -> bool:
        """True when a query returned more rows than the harness can see.

        `_summarize_tool_result` deliberately caps run_census_sql at
        `first_row` so a debug panel can't become an unbounded payload. That
        cap is right for the UI and blinds this check: a figure legitimately
        drawn from row 5 would look unsourced. When it applies, grounding is
        reported as inconclusive rather than failed — claiming a fabrication
        we cannot actually see would be its own dishonesty.
        """
        return any(
            c["tool"] == "run_census_sql"
            and c["ok"]
            and ((c.get("summary") or {}).get("row_count") or 0) > 1
            for c in self.tool_calls
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
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            f"terminal={obs.terminal}",
        )

    if check.type == CheckType.NO_TOOL_ERRORS:
        failed = [c.get("tool", "unknown") for c in obs.tool_calls if not c.get("ok")]
        observed = f"{len(obs.tool_calls)} tool calls, {len(failed)} failed"
        if failed:
            observed += ": " + ", ".join(failed)
        return CheckResult(check=check, passed=not failed, observed=observed)

    if check.type == CheckType.ANSWER_CONTAINS:
        passed = expected.lower() in obs.final_answer.lower()
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            obs.final_answer[:200],
        )

    if check.type == CheckType.ANSWER_REQUIRED:
        passed = bool(obs.final_answer.strip())
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            obs.final_answer[:200],
        )

    if check.type == CheckType.VARIABLE_RESOLVED:
        passed = expected in obs.tool_evidence
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            obs.tool_evidence[:200],
        )

    if check.type == CheckType.GEO_RESOLVED:
        passed = expected in obs.tool_evidence
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            obs.tool_evidence[:200],
        )

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
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            (
                f"{len(obs.final_turn_tool_calls)} tool calls on final turn "
                f"({len(obs.tool_calls)} across scenario), via {mechanism}; "
                f"{obs.final_answer[:100]}"
            ),
        )

    if check.type == CheckType.EXPECT_CLARIFYING_QUESTION:
        # Operationalized as: the turn ends by asking something, and never
        # attempted a query — even a failed query means the agent acted on an
        # unresolved interpretation instead of asking first.
        attempted_sql = any(c["tool"] == "run_census_sql" for c in obs.tool_calls)
        passed = "?" in obs.final_answer and not attempted_sql
        return _check_result(
            check,
            EvalOutcome.PASS if passed else EvalOutcome.FAIL,
            f"attempted_sql={attempted_sql}; {obs.final_answer[:120]}",
        )

    if check.type == CheckType.NO_MEDIAN_AGGREGATION:
        for call in obs.tool_calls:
            if call["tool"] != "run_census_sql":
                continue
            try:
                args = json.loads(call.get("args", ""))
                sql = args["sql"]
                if not isinstance(sql, str):
                    raise TypeError("sql is not a string")
                if _aggregates_variable(sql, expected):
                    return _check_result(
                        check,
                        EvalOutcome.FAIL,
                        f"{expected} appears beneath SUM or AVG: {sql[:160]}",
                    )
            except (json.JSONDecodeError, KeyError, ParseError, TypeError, ValueError) as exc:
                return _check_result(
                    check,
                    EvalOutcome.FAIL,
                    f"could not score recorded SQL argument: {exc}",
                )
        return _check_result(
            check,
            EvalOutcome.PASS,
            f"{expected} was not aggregated with SUM or AVG",
        )

    if check.type == CheckType.JUDGE_GROUNDEDNESS:
        # Implemented for the numeric half only — see _grounding_check. Every
        # scenario gets this appended automatically, so declaring it on a
        # scenario is redundant rather than wrong.
        return _grounding_check(obs)

    return _check_result(check, EvalOutcome.FAIL, f"unknown check {check.type}")


def _check_result(
    check: Check,
    outcome: EvalOutcome,
    observed: str,
) -> CheckResult:
    return CheckResult(
        check=check,
        passed=outcome == EvalOutcome.PASS,
        outcome=outcome,
        observed=observed,
    )


def _scenario_outcome(checks: list[CheckResult]) -> EvalOutcome:
    outcomes = {
        c.outcome or (EvalOutcome.PASS if c.passed else EvalOutcome.FAIL)
        for c in checks
    }
    if EvalOutcome.FAIL in outcomes:
        return EvalOutcome.FAIL
    if EvalOutcome.INCONCLUSIVE in outcomes:
        return EvalOutcome.INCONCLUSIVE
    return EvalOutcome.PASS


def _aggregates_variable(sql: str, variable_id: str) -> bool:
    tree = parse_one(sql, read="snowflake")
    for aggregate in tree.find_all(exp.Sum, exp.Avg):
        if any(
            column.name.lower() == variable_id.lower()
            for column in aggregate.find_all(exp.Column)
        ):
            return True
    return False


# --------------------------------------------------------------------------
# Numeric grounding — CLAUDE.md rule 2, the invariant the whole architecture
# exists to protect, and until now the one with no automated enforcement
# anywhere in the project.
#
# Deliberately deterministic rather than an LLM judge. For *numeric* claims
# there is nothing to judge: either the figure is in the rows the query
# returned or it is not. A judge would cost money, need calibrating against
# human labels before its scores meant anything, and be less reliable at the
# one thing plain arithmetic does perfectly. The judge is still the right
# tool for the prose half (is the vintage stated, is the median explanation
# an actual explanation), and that half remains unbuilt.
# --------------------------------------------------------------------------

# The boundaries matter: without them `B19013e1` yields the "figure" 19013 and
# every answer that cites a variable id is reported as a fabrication. Found
# exactly that way, on the first live run after this check was added.
_FIGURE_RE = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")

# Below this many digits the token is almost never a data claim: "5-year",
# "2 counties", "19% higher". Cheap precision at a known cost in recall, and a
# check that cries wolf is a check people delete.
_MIN_CLAIM_DIGITS = 4
_ROUNDING_TOLERANCE = 0.01


def _integer_digits(token: str) -> str:
    return token.replace(",", "").split(".")[0]


def _is_vintage_year(token: str) -> bool:
    """2020, 2016 and friends are vintage framing, not claims about rows."""
    digits = _integer_digits(token)
    return len(digits) == 4 and 1900 <= int(digits) <= 2100


def _claimed_figures(answer: str) -> list[str]:
    seen: list[str] = []
    for token in _FIGURE_RE.findall(answer):
        if len(_integer_digits(token)) < _MIN_CLAIM_DIGITS or _is_vintage_year(token):
            continue
        if token not in seen:
            seen.append(token)
    return seen


def _is_grounded(figure: float, returned: list[float]) -> bool:
    """Sourced if the figure is a returned value, or a simple arithmetic
    combination of two of them.

    The derived case is not generosity: CMP-01 legitimately answers "roughly
    199,000 higher" from two returned populations, and a check that failed it
    would be wrong. Tolerance covers the rounding the model applies when it
    says "roughly".
    """

    def close(a: float, b: float) -> bool:
        scale = max(abs(a), abs(b), 1.0)
        return abs(a - b) / scale <= _ROUNDING_TOLERANCE

    if any(close(figure, v) for v in returned):
        return True
    for a in returned:
        for b in returned:
            if a is b:
                continue
            if close(figure, abs(a - b)) or close(figure, a + b):
                return True
            if b:
                # Plain division is the mean substitution this product is
                # built around: aggregate income / households is exactly what
                # D-002 says to answer with when a median can't be aggregated.
                # Omitting it failed PM-02 and PM-08 on correct behaviour.
                if close(figure, a / b) or close(figure, a / b * 100):
                    return True
    return False


def _grounding_check(obs: Observation) -> CheckResult:
    check = Check(type=CheckType.JUDGE_GROUNDEDNESS)
    figures = _claimed_figures(obs.final_answer)

    if not figures:
        return _check_result(
            check, EvalOutcome.PASS, "no numeric claims in the answer"
        )

    returned = obs.returned_values
    # A figure echoed verbatim from anywhere in this turn's tool traffic (a
    # geo_id, a row the summary shows, a value in the SQL) is by definition
    # not invented, which is what rule 2 is about. Widening to the whole
    # evidence blob is more robust than trying to enumerate every identifier
    # shape a model might mention.
    evidence = obs.tool_evidence
    unsourced = [
        f
        for f in figures
        if not _is_grounded(float(f.replace(",", "")), returned)
        and f not in evidence
        and f.replace(",", "") not in evidence
    ]

    if not unsourced:
        return _check_result(
            check,
            EvalOutcome.PASS,
            f"{len(figures)} figure(s) traced to returned rows: {figures}",
        )

    if obs.has_unseen_rows:
        return _check_result(
            check,
            EvalOutcome.INCONCLUSIVE,
            (
                f"INCONCLUSIVE — {unsourced} not in the rows this harness can see, "
                "but a query returned more rows than TOOL_END exposes"
            ),
        )

    return _check_result(
        check,
        EvalOutcome.FAIL,
        (
            f"UNSOURCED {unsourced} — not in any row this turn's run_census_sql "
            f"returned (saw {returned or 'no rows at all'})"
        ),
    )


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


async def _run_all(scenarios: list) -> EvalRun:
    """One complete pass over the set, scored, as a single EvalRun.

    Deliberately returns a whole run rather than accumulating across repeats:
    with a live model a scenario is a coin with an unknown bias, so N passes
    are N independent measurements, not one measurement of N samples. Keeping
    them as separate EvalRuns is what lets the Evals tab show `2/3` for a
    flaky row without inventing a place to store that in the frozen contract.
    """
    results: list[EvalResult] = []
    for scenario in scenarios:
        print(f"running {scenario.id} ({scenario.category.value})…", flush=True)
        try:
            obs, elapsed = await _run_scenario(scenario)
        except Exception as exc:  # noqa: BLE001 — a crashed scenario is a red row, not a crashed run
            print(f"  !! {scenario.id} raised: {exc}", flush=True)
            results.append(
                EvalResult(
                    scenario_id=scenario.id,
                    category=scenario.category,
                    suite=scenario.suite,
                    outcome=EvalOutcome.FAIL,
                    passed=False,
                    checks=[
                        _check_result(c, EvalOutcome.FAIL, f"scenario raised: {exc}")
                        for c in scenario.checks
                    ],
                    answer_final="",
                    elapsed_s=0.0,
                )
            )
            continue

        # Rule 2 applies to every turn, so it is enforced on every scenario
        # rather than being something an author has to remember to declare.
        # Appended, not declared, precisely so a new example cannot omit it.
        check_results = [_score_check(c, obs) for c in scenario.checks]
        if not any(c.check.type == CheckType.JUDGE_GROUNDEDNESS for c in check_results):
            check_results.append(_grounding_check(obs))
        outcome = _scenario_outcome(check_results)
        results.append(
            EvalResult(
                scenario_id=scenario.id,
                category=scenario.category,
                suite=scenario.suite,
                outcome=outcome,
                passed=outcome == EvalOutcome.PASS,
                checks=check_results,
                answer_final=obs.final_answer,
                elapsed_s=round(elapsed, 1),
            )
        )
        mark = outcome.value.upper()
        print(f"  {mark} ({elapsed:.1f}s)", flush=True)

    # Every scenario in the set is run, so the denominator is simply the set.
    by_category: dict[str, list[bool]] = {}
    for r in results:
        by_category.setdefault(r.category.value, []).append(r.passed)

    n_executed = len(results)
    n_passed = sum(1 for r in results if r.passed)
    pass_rate = n_passed / n_executed if n_executed else 0.0

    print(f"\n{n_passed}/{n_executed} passed ({pass_rate:.0%})")
    for r in results:
        if not r.passed:
            failed = [c.check.type.value for c in r.checks if not c.passed]
            print(f"  RED  {r.scenario_id:8s} {', '.join(failed)}")

    return EvalRun(
        run_at=datetime.now(timezone.utc),
        git_sha=_git_sha(),
        results=results,
        pass_rate=pass_rate,
        by_category={k: sum(v) / len(v) for k, v in by_category.items()},
    )


def _write(run: EvalRun) -> str:
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = json.loads(run.model_dump_json())
    stamp = run.run_at.strftime("%Y%m%dT%H%M%SZ")
    # Two runs inside the same second would otherwise overwrite each other,
    # silently discarding a measurement — the one thing a repeat run exists to
    # collect. Unlikely at ~2.6 min per pass, cheap to make impossible.
    suffix = 0
    while (RESULTS_DIR / f"{stamp}.json").exists():
        suffix += 1
        stamp = f"{run.run_at.strftime('%Y%m%dT%H%M%SZ')}-{suffix}"
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(payload, indent=2))
    (RESULTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2))
    return stamp


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
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=(
            "Run the whole set N times, writing N separate result files. With "
            "a live model one run is not a measurement: a scenario that passes "
            "2 of 3 times is flaky, and a single run reports it as a clean "
            "pass or a clean fail. The Evals tab groups runs by commit and "
            "shows the ratio."
        ),
    )
    args = parser.parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    _require_credentials()
    only = _select([i.strip() for i in args.only.split(",") if i.strip()])

    scenarios = [s for s in GOLDEN_SCENARIOS if s.id in only] if only else GOLDEN_SCENARIOS

    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"\n=== run {i + 1} of {args.repeat} ===", flush=True)
        run = await _run_all(scenarios)

        # A filtered run must never touch evals/results/. Its pass rate is over
        # a hand-picked subset, so writing it would overwrite the committed
        # artifact with a number that looks like a full run and isn't. Rule 20's
        # "red rows are kept" only means anything if latest.json is a whole run.
        if only:
            print("\n--only: filtered run, nothing written to evals/results/")
            continue

        stamp = _write(run)
        print(f"wrote evals/results/{stamp}.json and latest.json")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
