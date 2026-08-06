# Golden-set evals

## What this is

`scenarios.py` holds **11 of the 30 golden scenarios designed in
`docs/plans/02-prd.md` §7** — verbatim: the PRD's own IDs, its own turn
text, its own expectations. `run_evals.py` drives them against the real
`agent_turn` (real Anthropic, real Snowflake, real guardrail), scores the
deterministic checks, and writes an `EvalRun` (`src/contracts.py`) to
`results/`.

```bash
make eval          # needs .env credentials — this is a live-call harness
```

The Evals tab in the web UI renders `results/latest.json` directly.

## Provenance, stated plainly

The scenario *design* — all 30, across 9 categories, each grounded in a
verified dataset fact — predates implementation. It was authored in commit
`ef2dd43` ("Scaffold repo: recon tooling, schema notes, and PRD") during
project scaffolding, before any agent code existed. That ordering matters:
the test cases were not reverse-engineered from a working system.

The *harness* and these 11 executable scenarios were written afterward, in
the final build session. The scenarios are transcriptions, not new
invention — but the checks attached to each one are mine, and they are
where the judgment (and the room for bias) lives. See "How much to trust a
100% pass rate" below.

An earlier version of this directory held 7 ad hoc scenarios I wrote
myself that **reused PRD scenario IDs for different questions** (e.g. a
`PM-01` that was actually closer to the PRD's `PM-02`, and a `GRD-01` that
doesn't exist in the PRD at all). That was misleading and has been deleted
outright rather than renamed — the current set is the real one.

## What's implemented, and what isn't

| PRD category | Designed | Here | Note |
|---|---|---|---|
| direct_fact | 5 | 2 | DF-01, DF-05 |
| comparison | 4 | 1 | CMP-01 |
| multi_turn | 4 | 1 | MT-01 (2 turns, one session) |
| ambiguous | 3 | 2 | AMB-01, AMB-02 |
| partial_match | 3 | 2 | PM-02, PM-03 |
| conflicting | 2 | **0** | Both need the decennial redistricting tables (D-004, issue #17) — cut, so there is nothing to run them against |
| unanswerable | 4 | 1 | UN-01 |
| off_topic | 3 | 1 | OT-01 |
| injection | 2 | 1 | INJ-02 |

`judge_groundedness` — the only LLM-judge check in the design (issue #21) —
is **not implemented**. No scenario carries it. If one did, the scorer
fails it loudly with "not implemented" rather than skipping it silently, so
it can never inflate a pass rate.

## How much to trust a 100% pass rate

The latest run is 11/11. That number should be read with the following
caveats, because a clean run can equally mean the checks are too loose:

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
