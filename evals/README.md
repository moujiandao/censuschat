# Golden-set evals

## What this is

`scenarios.py` holds **39 scenarios**. Two things vary independently, and
both are load-bearing enough that the file separates them and the Evals tab
renders them differently: **provenance** (was the case designed before the
code, or after it?) and **run state** (has it actually been executed?).

| | Count | Run state | Provenance |
|---|---|---|---|
| **Executed, PRD** | 12 | run, results recorded | Verbatim from `docs/plans/02-prd.md` §7 — the PRD's ids, turns, expectations, authored before any agent code existed |
| **Executed, authored** | 2 | run, results recorded | `UN-08`, `PM-08` — restore red rows deleted in `4170f0a`, written after the system worked |
| **Pending** | 25 | **never run** | Authored 2026-08-06, after the system existed |

A pass on a PRD row is stronger evidence than a pass on an authored one,
because a PRD row could not have been shaped to fit a system that already
worked. `PRD_SCENARIO_IDS` is what lets the UI say which is which.

`run_evals.py` drives the executed rows against the real `agent_turn` (real
Anthropic, real Snowflake, real guardrail), scores the deterministic checks,
and writes an `EvalRun` (`src/contracts.py`) to `results/`.

```bash
make eval          # needs .env credentials — this is a live-call harness
```

The Evals tab renders `results/latest.json` directly.

## Provenance, stated plainly

**The 11 executed rows.** The scenario *design* — all 30 in PRD §7, across
9 categories, each grounded in a verified dataset fact — predates
implementation. It was authored in commit `ef2dd43` ("Scaffold repo: recon
tooling, schema notes, and PRD"), before any agent code existed. That
ordering matters: those test cases were not reverse-engineered from a
system that already passed them. The 11 here are transcriptions of that
design, though the *checks* attached to each are mine.

**The 25 pending rows.** Authored after the code existed, to cover classes
the assignment emphasises that PRD §7 left thin: injection beyond one
shape, malformed input, NULL and top-coded values, multi-turn drift, and a
worst-case comparison against the 60s bound. They carry a bias risk the
PRD rows do not — I wrote both the system and these cases — and they are
**unverified in both directions**: never run, so neither their expected
behaviour nor their checks have met reality. They are a specification, not
evidence. `status="pending"` exists precisely so that cannot be blurred
(D-018).

**A prior mistake, recorded because it is the kind that survives.** An
earlier version of this directory held 7 ad hoc scenarios that **reused
PRD ids for different questions** (a `PM-01` nearer the PRD's `PM-02`, a
`GRD-01` absent from the PRD entirely). It looked like the golden set and
was not. It surfaced only because someone asked directly who wrote the
golden set. Deleted outright rather than renamed. The current id scheme
starts above the PRD's maximum in every category, so a new row can never
collide with a PRD one — including the 19 PRD rows still unimplemented.

## Pass rate excludes pending rows

`pass_rate` and `by_category` are computed from executed rows **before**
pending ones are appended to the artifact. An unrun backlog must not move a
real number in either direction: 25 pending scenarios dragging a genuine
11/11 down to 11/36 would be as dishonest as hiding them. A pending row is
serialized with `passed: false` and no checks, which means *no evidence*,
not *failed* — the `status` field is what distinguishes them, and the UI
renders on that, not on `passed`.

## What's implemented, and what isn't

Executed rows only. "PRD" counts rows traceable to PRD §7; "authored" counts
rows written later and run anyway (both restore red rows deleted in `4170f0a`).

| PRD category | Designed | PRD, executed | Authored, executed | Note |
|---|---|---|---|---|
| direct_fact | 5 | 2 | 0 | DF-01, DF-05 |
| comparison | 4 | 1 | 0 | CMP-01 |
| multi_turn | 4 | 1 | 0 | MT-01 (2 turns, one session) |
| ambiguous | 3 | 3 | 0 | AMB-01, AMB-02, AMB-03 — the full PRD set |
| partial_match | 3 | 2 | 1 | PM-02, PM-03; PM-08 is the deliberate red row |
| conflicting | 2 | **0** | 0 | Both need the decennial redistricting tables (D-004, issue #17) — cut, so there is nothing to run them against |
| unanswerable | 4 | 1 | 1 | UN-01; UN-08 pins the D-019 over-refusal fix |
| off_topic | 3 | 1 | 0 | OT-01 |
| injection | 2 | 1 | 0 | INJ-02 |

**Provenance is machine-readable, not prose.** `PRD_SCENARIO_IDS` in
`scenarios.py` is the source of truth for which ids came from PRD §7;
`/api/evals` joins it onto each stored result at read time so the Evals tab can
badge every row. It lives there rather than as a field on `EvalScenario`
because `src/contracts.py` is frozen (rule 12), and because a new field would
only label runs recorded from now on, while an id set retroactively labels the
result files already committed.

`judge_groundedness` — the only LLM-judge check in the design (issue #21) —
is **not implemented**. No scenario carries it. If one did, the scorer
fails it loudly with "not implemented" rather than skipping it silently, so
it can never inflate a pass rate.

## How much to trust the pass rate

The latest run is 13/14, with `PM-08` red on purpose. That number should be
read with the following caveats, because a clean row can equally mean the
check is too loose:

- **The strong checks.** `DF-05` asserts the literal string `581,348` —
  the real figure this share returns for Wyoming, confirmed by direct
  query. `GEO_RESOLVED`/`VARIABLE_RESOLVED` search the *tool evidence*,
  not the prose, so a model that merely names `B01003e1` without ever
  resolving it fails (there's a test for exactly that).
- **The weak check.** `PM-02` only asserts the answer contains "median"
  and doesn't error. The behavior the PRD actually wants — explaining that
  medians can't be aggregated and offering the true mean instead — is a
  `judge_groundedness` question, and that's cut. The live answer *did*
  explain it correctly, but the check would not have caught it if it
  hadn't. This is the clearest place where the harness under-verifies.
- **`EXPECT_REFUSAL` is looser than its name.** `CheckType` documents it as
  "guardrail fired"; the scorer operationalizes it as "zero tool calls,
  clean termination" — which is the behavioral property that matters
  (Snowflake never touched) but doesn't distinguish a guardrail refusal
  from the model declining on its own. Rather than gloss that, the scorer
  records which mechanism fired. The last run shows OT-01 and INJ-02 caught
  by the guardrail, and UN-01 passing the guardrail and being refused by
  the agent itself — a real distinction that a pass/fail alone would hide.
- **Author bias.** I wrote both the system and these checks in the same
  session. The scenario *questions* are insulated from that (they predate
  the code), but the thresholds are not.

Red rows are kept and reported, never dropped to make a run look clean
(CLAUDE.md rule 20). The current run has none; earlier ad hoc runs found
two real failures, one of which — a state-level mean substitution
exhausting the tool-loop cap — is recorded in `docs/reflection.md` and
still unfixed.
