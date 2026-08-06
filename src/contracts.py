"""Interface contracts for censuschat.

This file is the interface freeze. Implementations live in sibling modules;
every function here raises NotImplementedError until its module lands.
Claude Code: do not change signatures, field names, or enum members without
recording the deviation in docs/decisions.md and flagging it for approval.

Anything marked PROVISIONAL is gated on docs/schema-notes.md (recon evidence)
and must be resolved from that file, never from assumption.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# PROVISIONAL constants — fill from docs/schema-notes.md at M1, cite evidence
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# RESOLVED at M1 from docs/schema-notes.md. See docs/plans/02-prd.md §3.
# --------------------------------------------------------------------------

# 2020 vintage only (decision D-003). The 2019 tables are deliberately absent:
# block groups were redrawn for 2020 and the 5-year windows overlap 4 of 5
# years, so cross-vintage comparison is invalid. Excluding them here makes the
# invalid query impossible at the trust boundary, not merely discouraged in
# the prompt.
#
# Also deliberately absent:
#   2020_CBG_B99          — allocation/imputation-rate tables, never a valid
#                           answer to a demographic question
#   2019_CBG_PATTERNS     — SafeGraph foot-traffic data, not Census
#   2020_CBG_GEOMETRY_WKT — no map feature in scope; WKT bloats payloads
_DEMOGRAPHIC_TABLES: tuple[str, ...] = (
    "B01", "B02", "B03", "B07", "B08", "B09", "B11", "B12", "B14", "B15",
    "B16", "B17", "B19", "B20", "B21", "B22", "B23", "B24", "B25", "B27",
    "B28", "B29",
    "C02", "C15", "C16", "C17", "C21", "C24",
)

_METADATA_TABLES: tuple[str, ...] = (
    "METADATA_CBG_FIELD_DESCRIPTIONS",
    "METADATA_CBG_FIPS_CODES",
    "METADATA_CBG_GEOGRAPHIC_DATA",
)

# Compared against catalog.db.name with quotes stripped. The physical
# identifiers require double-quoting in SQL because they begin with a digit:
#   US_CENSUS.PUBLIC."2020_CBG_B01"
ALLOWED_TABLES: frozenset[str] = frozenset(
    [f"US_CENSUS.PUBLIC.2020_CBG_{t}" for t in _DEMOGRAPHIC_TABLES]
    + [f"US_CENSUS.PUBLIC.2020_{t}" for t in _METADATA_TABLES]
    # M3 (decision D-004) adds, once VariableHit.source is populated:
    #   US_CENSUS.PUBLIC.2020_REDISTRICTING_CBG_DATA
    #   US_CENSUS.PUBLIC.2020_REDISTRICTING_METADATA_CBG_FIELD_DESCRIPTIONS
)

# VERIFIED EMPTY — this is a finding, not an unfilled blank. Do not "fix" it.
# This loader represents suppression as real SQL NULL, not an ACS jam code:
# B19013e1 across 220,333 rows has MIN=2499, zero negative values, and 8,299
# NULLs. No -666666666, no 999999999 (schema-notes §6). normalize_value
# therefore keys on `raw is None`, not on a code table.
SENTINEL_CODES: dict[float, str] = {}

# Census top-codes are a separate concept from suppression: a real value
# carrying special meaning. MAX(B19013e1) = 250001 means "$250,000 or more",
# with 776 CBGs sitting exactly there. Rendering it as "$250,001" is wrong.
TOP_CODES: dict[str, float] = {"B19013": 250001.0}

# ACS 2016–2020 5-year — the latest vintage with full coverage (242,335 CBGs,
# 0 null population, 8,164 metadata fields). CBG-level ACS is published only
# as a 5-year rolling estimate, so a number is never a point-in-time count and
# must not be phrased as one.
DEFAULT_VINTAGE: int = 2020

MAX_RECOVERY_RETRIES: int = 2      # locked: bounded recovery loop
TURN_DEADLINE_S: float = 50.0      # locked: soft watchdog under the 60s cap
SQL_ROW_LIMIT: int = 200           # LIMIT injected by the gate when absent
SQL_STATEMENT_TIMEOUT_S: int = 25  # Snowflake session STATEMENT_TIMEOUT
SNOWFLAKE_CONNECT_TIMEOUT_S: int = 10  # bounds the login/auth handshake only


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

class GeoLevel(str, Enum):
    """RESOLVED at M1. Every level here is derivable from the 12-char
    CENSUS_BLOCK_GROUP by string truncation (schema-notes §2):
    tract = SUBSTR(...,1,11), county = SUBSTR(...,1,5), state = SUBSTR(...,1,2).

    PLACE, CBSA and ZCTA were REMOVED: 2020_METADATA_CBG_FIPS_CODES carries
    only STATE, STATE_FIPS, COUNTY_FIPS, COUNTY, CLASS_CODE. No place, CBSA or
    ZCTA identifier exists anywhere in the 73 objects, and there is no
    crosswalk to derive one. City questions get the honest redirect in D-005.
    """

    NATION = "nation"
    STATE = "state"
    COUNTY = "county"
    TRACT = "tract"
    BLOCK_GROUP = "block_group"


class GuardrailAction(str, Enum):
    ALLOW = "allow"
    REFUSE = "refuse"


class RefusalCategory(str, Enum):
    OFF_TOPIC = "off_topic"
    ADVERSARIAL = "adversarial"      # injection, jailbreak, SQL-through-chat
    INAPPROPRIATE = "inappropriate"


class EventType(str, Enum):
    """SSE event types streamed to the client. A turn MUST terminate with
    DONE or ERROR — never end a stream silently."""

    TOKEN = "token"            # data: {"text": str}
    TOOL_START = "tool_start"  # data: {"tool": str, "args_preview": str}
    # `summary` is a bounded, USER-SAFE per-tool result digest (hit counts,
    # resolved geo_ids, row_count/columns/first_row, or a sanitized error) —
    # additive to this free-form `data` dict, not a signature change. It
    # powers the Flow Diagram / Trace Logging tabs; see
    # src/agent.py:_summarize_tool_result for the safety and bounding rules.
    TOOL_END = "tool_end"      # data: {"tool": str, "ok": bool, "elapsed_ms": int, "summary": dict}
    STATUS = "status"          # data: {"message": str}  e.g. "warehouse resuming"
    ERROR = "error"            # data: {"message": str}  user-safe text only
    DONE = "done"              # data: {"elapsed_ms": int}


class ScenarioCategory(str, Enum):
    """Golden-set taxonomy. Mirrors the assignment's problem classes plus the
    happy paths; counts per category live in docs/plans/01-architecture.md."""

    DIRECT_FACT = "direct_fact"
    COMPARISON = "comparison"
    MULTI_TURN = "multi_turn"
    AMBIGUOUS = "ambiguous"
    PARTIAL_MATCH = "partial_match"
    CONFLICTING = "conflicting"
    UNANSWERABLE = "unanswerable"
    OFF_TOPIC = "off_topic"
    INJECTION = "injection"
    # Added post-PRD (D-018): malformed/hostile *input shape* rather than
    # hostile intent — empty, whitespace-only, very long, non-English,
    # emoji-only, embedded control characters. The assignment's production
    # bar asks that none of these crash or hang.
    STRESS = "stress"


class CheckType(str, Enum):
    EXPECT_REFUSAL = "expect_refusal"                      # guardrail fired
    EXPECT_CLARIFYING_QUESTION = "expect_clarifying_question"
    VARIABLE_RESOLVED = "variable_resolved"  # expected variable_id in tool trace
    GEO_RESOLVED = "geo_resolved"            # expected geo_id in tool trace
    ANSWER_CONTAINS = "answer_contains"      # grounded number/substring in answer
    NO_UNHANDLED_ERROR = "no_unhandled_error"
    JUDGE_GROUNDEDNESS = "judge_groundedness"  # the ONLY LLM-judge check; binary


class SqlViolation(str, Enum):
    PARSE_ERROR = "parse_error"
    NOT_SELECT = "not_select"
    MULTI_STATEMENT = "multi_statement"
    TABLE_NOT_ALLOWED = "table_not_allowed"
    BANNED_CONSTRUCT = "banned_construct"   # e.g. INTO, session vars, procedures


# --------------------------------------------------------------------------
# Tool I/O models
# --------------------------------------------------------------------------

class VariableHit(BaseModel):
    """A variable-search result.

    geo_levels encodes AGGREGATION VALIDITY, not availability (change C-3).
    Every ACS variable in this share is physically available at all five
    levels via roll-up, so an availability reading would make the field a
    constant. Under the validity reading it carries the single most important
    correctness rule in the dataset:

        count variables  -> all five levels (they roll up by SUM)
        median variables -> [BLOCK_GROUP] only

    Averaging block-group medians to county or state is methodologically
    invalid and this share carries no published county/state medians. 28
    median table numbers are affected. Where an aggregate table exists, the
    agent computes a true mean instead — SUM(B19025e1)/SUM(B11001e1) — and
    states the substitution.
    """

    variable_id: str
    label: str
    description: str = ""
    geo_levels: list[GeoLevel] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    score: float  # FTS rank; higher is better
    # ACS 5-year estimate vs decennial full count (change C-2, decision D-004).
    # Without this the agent can silently mix a full count with a 5-year
    # estimate — the exact failure the `conflicting` goldens probe.
    source: Literal["acs", "decennial"] = "acs"


class VariableSearchResult(BaseModel):
    query: str
    hits: list[VariableHit] = Field(default_factory=list)
    truncated: bool = False


class GeoCandidate(BaseModel):
    geo_id: str
    name: str                        # canonical, e.g. "Springfield city, Illinois"
    level: GeoLevel
    state: str | None = None         # postal abbr, for disambiguation display


class GeoResolution(BaseModel):
    query: str
    candidates: list[GeoCandidate] = Field(default_factory=list)
    ambiguous: bool = False          # True → agent MUST ask, never silently pick


class SqlGateResult(BaseModel):
    ok: bool
    sql: str                         # sanitized (LIMIT injected) when ok
    violations: list[SqlViolation] = Field(default_factory=list)
    detail: str | None = None


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: int = 0


class CensusValue(BaseModel):
    """Normalized cell value. RESOLVED at M1.

    Suppression in this share is real SQL NULL, not a jam code, so
    normalize_value keys on `raw is None`. The `sentinel` field is retained
    for interface stability but stays None unless SENTINEL_CODES is ever
    populated.
    """

    raw: Any
    value: float | None              # None when suppressed
    suppressed: bool = False
    sentinel: str | None = None      # label of matched sentinel code, if any
    # True when the raw value is a Census top-code — a real number carrying
    # special meaning (change C-1). 250001 means "$250,000 or more", not
    # $250,001. Callers must render the band, never the bare figure.
    top_coded: bool = False


class GuardrailVerdict(BaseModel):
    action: GuardrailAction
    category: RefusalCategory | None = None
    reason: str | None = None        # short, user-safe
    latency_ms: int | None = None


# --------------------------------------------------------------------------
# Chat / session models
# --------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime | None = None


class ChatEvent(BaseModel):
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    session_id: str
    messages: list[ChatMessage] = Field(default_factory=list)


class SnapshotInfo(BaseModel):
    built_at: datetime
    variables_rows: int
    geo_rows: int
    elapsed_s: float
    source_tables: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Eval models — evals/results/latest.json is an EvalRun; the Evals tab
# renders latest vs. previous EvalRun directly.
# --------------------------------------------------------------------------

class Check(BaseModel):
    type: CheckType
    expected: str | None = None      # substring / variable_id / geo_id per type


class EvalScenario(BaseModel):
    id: str
    category: ScenarioCategory
    turns: list[str]                 # 1..n user turns, driven sequentially
    checks: list[Check]
    # D-018. "pending" = authored but never executed: a specification of
    # intended behaviour, not evidence of it. run_evals skips these and
    # excludes them from the pass-rate denominator, so an unrun backlog can
    # never dilute or inflate a real result. Defaulted so every pre-existing
    # scenario stays "executed" without being touched.
    status: Literal["pending", "executed"] = "executed"
    notes: str | None = None


class CheckResult(BaseModel):
    check: Check
    passed: bool
    observed: str | None = None


class EvalResult(BaseModel):
    scenario_id: str
    category: ScenarioCategory
    passed: bool                     # all checks passed
    checks: list[CheckResult]
    answer_final: str = ""
    elapsed_s: float = 0.0
    # D-018. Mirrors EvalScenario.status so the Evals tab can render the
    # unrun backlog alongside real results without either being mistaken
    # for the other. A "pending" row always has passed=False and no
    # CheckResults — absence of evidence, not a failure.
    status: Literal["pending", "executed"] = "executed"


class EvalRun(BaseModel):
    run_at: datetime
    git_sha: str
    results: list[EvalResult]
    pass_rate: float
    by_category: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class SnapshotError(RuntimeError):
    """Snapshot build failed. The app must still boot and report degraded
    status via /api/health — never crash on startup because of this."""


class SqlRejected(RuntimeError):
    """SQL failed the gate. The agent sees this as a tool-error string and
    gets a bounded rewrite opportunity."""

    def __init__(self, result: SqlGateResult) -> None:
        super().__init__(result.detail or ", ".join(v.value for v in result.violations))
        self.result = result


# --------------------------------------------------------------------------
# Data layer
# --------------------------------------------------------------------------

def build_snapshot(force: bool = False) -> SnapshotInfo:
    """Pull the attributes/metadata table and geography index from Snowflake
    into local SQLite (FTS5 over variable label+description; plain index over
    geo names). Runs at app startup; no-op when a snapshot exists and
    force=False. Raises SnapshotError on failure — caller boots degraded.
    """
    raise NotImplementedError


def normalize_value(raw: Any, variable_id: str | None = None) -> CensusValue:
    """Map a raw Snowflake cell to CensusValue. Pure function: TDD target.

    Suppression is SQL NULL in this share (SENTINEL_CODES is verified empty),
    so `raw is None` -> value=None, suppressed=True. NULL means "not
    reported" and must never be coerced to 0.

    When variable_id is supplied and its table number appears in TOP_CODES
    with a matching value, top_coded=True — the caller renders the band
    ("$250,000 or more"), never the bare number. Neither a suppressed nor a
    top-coded value is ever rendered to the user as a plain figure.
    """
    if raw is None:
        return CensusValue(raw=raw, value=None, suppressed=True)

    value = float(raw)
    top_coded = False
    if variable_id:
        match = re.match(r"^([A-Z]+\d+)", variable_id)
        if match and TOP_CODES.get(match.group(1)) == value:
            top_coded = True

    return CensusValue(raw=raw, value=value, suppressed=False, top_coded=top_coded)


# --------------------------------------------------------------------------
# The agent's three tools (exactly three — CLAUDE.md rule)
# --------------------------------------------------------------------------

def search_census_variables(query: str, limit: int = 10) -> VariableSearchResult:
    """FTS5 search over the local variable snapshot. Returns hits with
    coverage metadata (geo_levels, years) so partial-match questions become
    honest answers ('exists at county level but not tract'). Local only —
    never touches Snowflake.
    """
    raise NotImplementedError


def resolve_geography(
    name: str, level_hint: GeoLevel | None = None
) -> GeoResolution:
    """Lookup over the local geography index. Multiple plausible candidates
    across states/levels → ambiguous=True and the agent asks the user;
    never silently pick one. Local only. Deterministic ranking: TDD target.
    """
    raise NotImplementedError


def run_census_sql(sql: str) -> QueryResult:
    """The ONLY code path that touches Snowflake at request time.
    Pipeline: validate_sql → execute read-only with STATEMENT_TIMEOUT set →
    QueryResult. Raises SqlRejected when the gate fails. User text is never
    interpolated into SQL anywhere in the codebase.
    """
    raise NotImplementedError


# --------------------------------------------------------------------------
# SQL gate (pure, no I/O — primary TDD target)
# --------------------------------------------------------------------------

def validate_sql(
    sql: str, allowed_tables: frozenset[str] = ALLOWED_TABLES
) -> SqlGateResult:
    """sqlglot parse with dialect='snowflake'. Enforce: parses cleanly;
    exactly one statement; statement is a SELECT (CTEs allowed, must resolve
    to SELECT); every referenced table is in allowed_tables; no banned
    constructs (DML/DDL/INTO/CALL/session vars); inject LIMIT SQL_ROW_LIMIT
    when absent. Returns sanitized SQL on ok=True.
    """
    raise NotImplementedError


# --------------------------------------------------------------------------
# Guardrail
# --------------------------------------------------------------------------

def classify_input(
    message: str, recent_turns: list[ChatMessage]
) -> GuardrailVerdict:
    """Haiku fast-fail pre-classifier. MUST receive recent_turns: bare
    follow-ups ('what about women?') are on-topic in context. Refuse only
    clearly off-topic / adversarial / inappropriate input; borderline →
    ALLOW (the agent's grounding rules are layer two; the SQL gate is the
    hard boundary). Target <1.5s. On classifier error or timeout → ALLOW
    (fail open) with reason='classifier_unavailable'.
    """
    raise NotImplementedError


# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------

async def agent_turn(
    session_id: str, user_message: str
) -> AsyncIterator[ChatEvent]:
    """Full pipeline for one user turn:
    guardrail → full-history replay → Sonnet tool loop (three tools; at most
    MAX_RECOVERY_RETRIES recovery attempts after a SQL error or zero-row
    result, then honest failure describing what was tried) → grounded answer.

    Invariants: every numeric claim originates from this turn's QueryResult
    rows; zero rows never becomes a number; TURN_DEADLINE_S watchdog stops
    further tool calls and yields an honest partial answer; the stream always
    terminates with DONE or ERROR; every tool call emits TOOL_START/TOOL_END;
    the whole turn is one Langfuse trace (session_id in metadata, spans for
    guardrail, each tool call, and each model call).
    """
    raise NotImplementedError
    yield ChatEvent(type=EventType.DONE)  # pragma: no cover — async-gen marker


# --------------------------------------------------------------------------
# Session store (SQLite)
# --------------------------------------------------------------------------

def get_session(session_id: str) -> Session:
    """Fetch (or create empty) session. Full history replay is the state
    model — no summarization, no extraction; tradeoff documented in the
    reflection."""
    raise NotImplementedError


def append_message(session_id: str, msg: ChatMessage) -> None:
    raise NotImplementedError
