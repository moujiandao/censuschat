# Golden-set evals

## What this is

`scenarios.py` holds **14 examples**, every one of which has actually been
run. `run_evals.py` drives them against the real `agent_turn` (real
Anthropic, real Snowflake, real guardrail), scores the deterministic checks,
and writes an `EvalRun` (`src/contracts.py`) to `results/`.

```bash
make eval                                   # one pass, ~2.6 min
python -m evals.run_evals --repeat 3        # three passes, three result files
python -m evals.run_evals --only DF-05      # debugging; writes nothing
```

The Evals tab renders `results/latest.json` directly, plus a **run history**
matrix built from every file in `results/`.

## Tracking whether it is improving

Each run writes its own timestamped file, so `results/` is already a version
history keyed by `git_sha`. The tab draws it as a scenario × commit grid.

**Compare rows, not the overall pass rate.** The first two recorded runs are
100% and the third is 93%, which looks like a regression and isn't: the set
grew from 11 examples to 14, and one of the three added rows is deliberately
red. An aggregate rate is only comparable across runs whose denominator
didn't change, and it silently stops being comparable the moment you add an
example. The per-scenario grid has no such problem, and the delta line under
it reports fixes, regressions and additions as three separate things.

**One run is not a measurement.** With a live model a scenario is a coin with
an unknown bias: `PM-08` passes roughly two runs in three. A single run
reports that as a clean pass or a clean fail, so a one-row delta on a 14-row
set is inside the noise. `--repeat N` runs the whole set N times and writes N
files; runs of the same commit collapse into one column showing how many
passed (`2/3`), rendered as its own state so flake can't be mistaken for a
fix. Cost of honesty: N times the API spend and wall clock.

Two gaps worth knowing. `EvalRun` records `git_sha` but not the model ids, so
a change in pass rate could come from Anthropic rather than from you; adding
them is a rule 12 contracts change. And the history has no notion of a
baseline, so the delta line only ever compares the two most recent commits.

**There is no backlog.** An earlier version of this set carried 25 further
scenarios that were authored but never executed, marked `status="pending"`.
They were removed: a scenario that has never run is a wish, not a test, and
sitting them next to real results made the set harder to read than the
coverage claim was worth. The stored result files still contain those rows
as history; `/api/evals` skips them.

## Where the examples come from

Twelve are transcribed verbatim from `docs/plans/02-prd.md` §7 — the PRD's
own ids, turns, and expectations. That design was authored in commit
`ef2dd43` during scaffolding, **before any agent code existed**, so those
cases were not reverse-engineered from a system that already passed them.
That is also why the ids have gaps (`DF-01` then `DF-05`): they are the
PRD's numbering, and only 12 of its 30 were implemented.

`UN-08` and `PM-08` were added later to pin two known failures. Both carry
that history in their `notes`.

The scenario *questions* mostly predate the code. The *checks* attached to
each one are mine, written in the same session as the system, which is where
the room for bias actually lives.

**A prior mistake, recorded because it is the kind that survives.** An
earlier version of this directory held 7 ad hoc scenarios that **reused PRD
ids for different questions** (a `PM-01` nearer the PRD's `PM-02`, a `GRD-01`
absent from the PRD entirely). It looked like the golden set and was not. It
surfaced only because someone asked directly who wrote the golden set.
Deleted outright rather than renamed. A test now pins that ids are unique.

## What isn't covered

- `conflicting` (`CF-01`, `CF-02`) — both need the decennial redistricting
  tables (D-004, issue #17), which were cut. Nothing to run them against.
- `judge_groundedness`, the only LLM-judge check in the design (issue #21),
  is **not implemented**. No example carries it. If one did, the scorer fails
  it loudly with "not implemented" rather than skipping it silently, so it
  can never inflate a pass rate. The practical consequence: nothing here
  automatically verifies that every number in an answer traces to a returned
  row, which is the single largest hole in the strategy.
- 18 of the PRD's 30 designed scenarios were never implemented.

## How much to trust the pass rate

The latest run is 13/14, with `PM-08` red on purpose. Read it with these
caveats, because a green row can equally mean the check is too loose:

- **The strong checks.** `DF-05` asserts the literal string `581,348`, the
  real figure this share returns for Wyoming, confirmed by direct query.
  `GEO_RESOLVED`/`VARIABLE_RESOLVED` search the *tool evidence*, not the
  prose, so a model that merely names `B01003e1` without ever resolving it
  fails (there is a test for exactly that).
- **The weak check.** `PM-02` only asserts the answer contains "median" and
  doesn't error. What the PRD actually wants — explaining that medians can't
  be aggregated and offering the true mean instead — is a
  `judge_groundedness` question, and that's cut. The live answer *did*
  explain it correctly, but the check would not have caught it if it hadn't.
  This is the clearest place where the harness under-verifies.
- **`EXPECT_REFUSAL` is looser than its name.** `CheckType` documents it as
  "guardrail fired"; the scorer operationalizes it as "zero tool calls, clean
  termination" — the behavioral property that matters (Snowflake never
  touched), but it doesn't distinguish a guardrail refusal from the model
  declining on its own. The scorer records which mechanism fired rather than
  glossing it: the last run shows `OT-01` and `INJ-02` caught by the
  guardrail, and `UN-01` passing the guardrail and being refused by the agent
  itself.
- **Author bias.** I wrote both the system and these checks in the same
  session. The questions are largely insulated from that; the thresholds are
  not.

**Red rows are kept and reported**, never dropped to make a run look clean
(CLAUDE.md rule 20). The current run has one: `PM-08`, the state-level mean
substitution that exhausts the 8-round tool-loop cap. It was deleted from the
set in `4170f0a` and restored deliberately, because a run with the known
failure removed is a worse artifact than one that is 13/14. Its `notes` field
carries the triage: retrieval was fixed (D-020), the binding constraint moved
to the round cap, and raising that cap trades against the 50s watchdog — so
it is committed red and flaky (green in roughly two live runs of three)
rather than quietly tuned green.

**Flake is disclosed, not averaged.** A scenario that passes 2 of 3 runs is
recorded as failing when it fails. Re-running until green would make the
artifact a selection effect rather than a measurement.
