"""Interface contracts for censuschat.

This file is the interface freeze. Implementations live in sibling modules;
every function here raises NotImplementedError until its module lands.
Claude Code: do not change signatures, field names, or enum members without
recording the deviation in docs/decisions.md and flagging it for approval.

Anything marked PROVISIONAL is gated on docs/schema-notes.md (recon evidence)
and must be resolved from that file, never from assumption.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# PROVISIONAL constants — fill from docs/schema-notes.md at M1, cite evidence
# --------------------------------------------------------------------------

# Fully-qualified Snowflake table names the SQL gate accepts. Empty default
# means validate_sql rejects everything — safe until recon fills it.
ALLOWED_TABLES: frozenset[str] = frozenset()  # PROVISIONAL

# ACS jam/sentinel values that mean "suppressed / not applicable", mapped to a
# short human label. Exact codes come from schema-notes (e.g. -666666666).
SENTINEL_CODES: dict[float, str] = {}  # PROVISIONAL

# Default ACS vintage the agent assumes (and states) when the user gives none.
DEFAULT_VINTAGE: int | None = None  # PROVISIONAL

MAX_RECOVERY_RETRIES: int = 2      # locked: bounded recovery loop
TURN_DEADLINE_S: float = 50.0      # locked: soft watchdog under the 60s cap
SQL_ROW_LIMIT: int = 200           # LIMIT injected by the gate when absent
SQL_STATEMENT_TIMEOUT_S: int = 25  # Snowflake session STATEMENT_TIMEOUT


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

class GeoLevel(str, Enum):
    """PROVISIONAL — confirm the exact levels present against schema-notes."""

    NATION = "nation"
    STATE = "state"
    COUNTY = "county"
    PLACE = "place"            # incorporated cities/towns
    TRACT = "tract"
    BLOCK_GROUP = "block_group"
    CBSA = "cbsa"              # metro/micro areas
    ZCTA = "zcta"


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
    TOOL_END = "tool_end"      # data: {"tool": str, "ok": bool, "elapsed_ms": int}
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
    variable_id: str
    label: str
    description: str = ""
    geo_levels: list[GeoLevel] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    score: float  # FTS rank; higher is better


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
    """Normalized cell value. PROVISIONAL until SENTINEL_CODES is filled."""

    raw: Any
    value: float | None              # None when suppressed / sentinel-coded
    suppressed: bool = False
    sentinel: str | None = None      # label of matched sentinel code, if any


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


def normalize_value(raw: Any) -> CensusValue:
    """Map a raw Snowflake cell to CensusValue using SENTINEL_CODES.
    Sentinel-coded values MUST come back suppressed=True, value=None —
    they are never rendered to the user as real numbers. PROVISIONAL until
    schema-notes supplies the codes. Pure function: TDD target.
    """
    raise NotImplementedError


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
