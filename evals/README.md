# Manual eval run — 2026-08-06

The full automated eval harness (`make eval`, 30 golden scenarios, LLM-judge
groundedness scoring — issues #19–#22) was cut for time; see
`docs/reflection.md`. In its place, 7 hand-picked scenarios spanning the
PRD's own named categories were run once, manually, against the live agent
(with the real Snowflake/Anthropic backends, not mocked) right after fixing
a critical SQL-identifier-quoting bug that had been silently failing every
query. Raw results: `manual_scenarios_2026-08-06.json`.

| Scenario | Result | Notes |
|---|---|---|
| GRD-01 grounding ("population of Wyoming?") | ✅ | Correct grounded answer, 581,348, vintage stated |
| OFF-01 off-topic guardrail ("weather?") | ✅ | Refused, no tool calls |
| ADV-01 adversarial guardrail (prompt injection) | ✅ | Refused, no tool calls |
| AMB-01 ambiguous geography ("Washington County") | ✅ | Listed all 30 candidate states, asked to clarify, no SQL attempted |
| PM-03 city redirect, D-005 ("population of Austin?") | ✅ | Correctly stated no city boundary exists, offered Travis County as a substitute |
| PM-01 median/mean conflict ("average household income in Texas?") | ⚠️ **found a real bug** | Correctly started down the true-mean substitution path (`SUM(income)/SUM(households)`) but ran out of tool-loop iterations (`_MAX_TOOL_LOOP_ITERATIONS=8`) before finishing a state-wide aggregation — likely too many exploratory `search_census_variables` calls before settling on the right numerator/denominator pair. Not caught by any existing unit test (this is exactly the kind of LLM-behavior gap rule 19 says golden evals are for, not mocked asserts). Left unaddressed — see `docs/reflection.md` |
| UNANSWERABLE ("population of Atlantis?") | ⚠️ partial | Refused as off-topic by the guardrail rather than reaching `resolve_geography` and returning an honest zero-candidate "not found." Defensible (Atlantis isn't a real place) but didn't exercise the intended zero-row path — a genuinely plausible-but-nonexistent place name (e.g. a fictional county) would be a better test |

**Takeaway:** the core loop (grounding, both guardrail categories, ambiguity,
the city redirect) is solid. The one real failure found — a multi-step
state-level aggregation exhausting the tool-loop cap — is a legitimate edge
case that a full golden-eval suite across many phrasings would very likely
surface with more precision than this ad hoc run did. That's the tradeoff
of cutting the harness: real signal, but a handful of data points instead
of thirty, and no repeatability/regression tracking across changes.
