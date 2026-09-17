"""One checked renter-share action. No model tools or durable offer schema."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from src import tools
from src.contracts import (
    DEFAULT_VINTAGE, TURN_DEADLINE_S, ChatEvent, ChatMessage, EventType,
    GeoCandidate, GeoLevel, QueryResult, VariableHit, VariableSearchResult,
)
from src.sessions import append_message
from src.tracing import TraceSpan, TurnTrace, record_turn_trace

logger = logging.getLogger(__name__)
LABEL = "Compare renter share"
EXPLANATION = "Compare the percentage of occupied homes that are rented in these same counties."
OFFER_TTL_S = 300
MAX_SESSIONS = 1024
# The observed fixed query takes about six seconds. This is a minimum reserve,
# not a claim that Snowflake always completes within ten seconds.
MIN_PREFLIGHT_TIME_S = 10


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _field_is(hit: VariableHit, title: str, universe: str, path: str) -> bool:
    expected = f"Universe: {universe}. Field: {title} Estimate {title} {universe} {path} {universe}"
    return (
        hit.label.lower() == title.lower()
        and _normalized(hit.description) == _normalized(expected)
        and hit.source == "acs" and hit.years == [DEFAULT_VINTAGE]
        and GeoLevel.COUNTY in hit.geo_levels
        and bool(re.fullmatch(r"[BC]\d{5,6}e\d+", hit.variable_id))
        and hit.physical_table == tools._physical_table_for_acs_variable(hit.variable_id)
    )


def _occupied_total(hit: VariableHit) -> bool:
    return _field_is(hit, "Tenure", "Occupied housing units", "Total")


@dataclass(frozen=True)
class Recipe:
    total: VariableHit
    renter: VariableHit


def discover_recipe(found: VariableSearchResult) -> Recipe | None:
    totals = [h for h in found.hits if _occupied_total(h)]
    renters = [h for h in found.hits if _field_is(h, "Tenure", "Occupied housing units", "Total Renter occupied")]
    if len(totals) != 1 or len(renters) != 1:
        return None
    if totals[0].physical_table != renters[0].physical_table:
        return None
    return Recipe(totals[0], renters[0])


def _prefix(node) -> bool:
    column = node.this if isinstance(node, (exp.Left, exp.Substring)) else None
    if not isinstance(column, exp.Column) or column.name.upper() != "CENSUS_BLOCK_GROUP":
        return False
    if isinstance(node, exp.Left):
        return node.expression == exp.Literal.number(5)
    return node.args.get("start") == exp.Literal.number(1) and node.args.get("length") == exp.Literal.number(5)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass
class TurnContext:
    totals: dict[str, VariableHit] = field(default_factory=dict)
    geographies: dict[str, GeoCandidate] = field(default_factory=dict)
    query: tuple[str, dict] | None = None
    query_count: int = 0
    failed: bool = False

    def observe(self, name: str, args: dict, result: dict, error: bool) -> None:
        self.failed |= error
        if error:
            return
        if name == "search_census_variables":
            for raw in result.get("hits", []):
                hit = VariableHit.model_validate(raw)
                if _occupied_total(hit):
                    self.totals[hit.variable_id] = hit
        elif name == "resolve_geography":
            candidates = result.get("candidates", [])
            if result.get("ambiguous") or len(candidates) != 1:
                self.failed = True
            else:
                county = GeoCandidate.model_validate(candidates[0])
                if county.level == GeoLevel.COUNTY and re.fullmatch(r"\d{5}", county.geo_id):
                    self.geographies[county.geo_id] = county
        elif name == "run_census_sql":
            self.query_count += 1
            self.query = (args.get("sql", ""), result)

    def counties(self) -> tuple[GeoCandidate, ...]:
        """Recognize only one SELECT: county prefix + SUM(total), two-ID IN filter.

        Reject joins, subqueries, extra predicates/metrics and unknown shapes.
        Result county IDs must agree with both the filter and resolved geography.
        """
        if self.failed or self.query_count != 1 or not self.query:
            return ()
        sql, result = self.query
        if result.get("truncated") or result.get("row_count") != 2 or len(result.get("rows", [])) != 2:
            return ()
        try:
            tree = parse_one(sql, read="snowflake")
            if not isinstance(tree, exp.Select) or len(tree.expressions) != 2:
                return ()
            if any(value for key, value in tree.args.items() if key not in {"expressions", "from_", "where", "group", "order", "limit"}):
                return ()
            table = tree.args["from_"].this
            if not isinstance(table, exp.Table):
                return ()
            prefix = next(e for e in tree.expressions if isinstance(e, exp.Alias) and _prefix(e.this))
            aggregate = next(e for e in tree.expressions if isinstance(e, exp.Alias) and isinstance(e.this, exp.Sum))
            column = aggregate.this.this
            if not isinstance(column, exp.Column) or column.name not in self.totals:
                return ()
            physical = ".".join([table.catalog, table.db, table.name]).upper()
            if physical != self.totals[column.name].physical_table.replace('"', '').upper():
                return ()
            where = tree.args["where"].this
            if not isinstance(where, exp.In) or not _prefix(where.this) or where.args.get("query"):
                return ()
            ids = tuple(e.this for e in where.expressions if isinstance(e, exp.Literal) and e.is_string)
            if len(ids) != 2 or len(where.expressions) != 2 or len(set(ids)) != 2:
                return ()
            if not all(i in self.geographies for i in ids):
                return ()
            groups = tree.args["group"].expressions
            if len(groups) != 1 or not (_prefix(groups[0]) or (isinstance(groups[0], exp.Column) and not groups[0].table and groups[0].name.lower() == prefix.alias.lower())):
                return ()
            rows = [{k.upper(): v for k, v in row.items()} for row in result["rows"]]
            if {r.get(prefix.alias.upper()) for r in rows} != set(ids):
                return ()
            if not all(_number(r.get(aggregate.alias.upper())) and r[aggregate.alias.upper()] >= 0 for r in rows):
                return ()
            return tuple(self.geographies[i] for i in ids)
        except (ValueError, KeyError, AttributeError, StopIteration, SqlglotError):
            return ()


def query_for(recipe: Recipe, counties: tuple[GeoCandidate, ...]) -> str:
    if len(counties) != 2 or len({c.geo_id for c in counties}) != 2 or not all(re.fullmatch(r"\d{5}", c.geo_id) for c in counties):
        raise ValueError("Expected two distinct validated counties")
    if discover_recipe(VariableSearchResult(query="tenure", hits=[recipe.total, recipe.renter])) is None:
        raise ValueError("Unsupported recipe")
    # Identifiers come from checked runtime metadata, FIPS from resolved geography.
    total, renter = recipe.total.variable_id, recipe.renter.variable_id
    ids = ",".join(f"'{c.geo_id}'" for c in counties)
    return f'''SELECT LEFT(g.CENSUS_BLOCK_GROUP,5) AS COUNTY_ID,
COUNT(*) AS REFERENCE_ROWS, COUNT(DISTINCT g.CENSUS_BLOCK_GROUP) AS UNIQUE_BLOCKS,
COUNT(h.CENSUS_BLOCK_GROUP) AS MATCHED_ROWS,
COUNT(h."{total}") AS TOTAL_VALUES, COUNT(h."{renter}") AS RENTER_VALUES,
SUM(CASE WHEN h."{renter}" < 0 OR h."{total}" < 0 OR h."{renter}" > h."{total}" THEN 1 ELSE 0 END) AS INVALID_VALUES,
SUM(h."{total}") AS OCCUPIED, SUM(h."{renter}") AS RENTED,
100.0 * SUM(h."{renter}") / NULLIF(SUM(h."{total}"), 0) AS RENTER_SHARE
FROM US_CENSUS.PUBLIC."{DEFAULT_VINTAGE}_METADATA_CBG_GEOGRAPHIC_DATA" g
LEFT JOIN {recipe.total.physical_table} h ON g.CENSUS_BLOCK_GROUP=h.CENSUS_BLOCK_GROUP
WHERE LEFT(g.CENSUS_BLOCK_GROUP,5) IN ({ids}) GROUP BY LEFT(g.CENSUS_BLOCK_GROUP,5)'''


def valid_result(result: QueryResult, counties: tuple[GeoCandidate, ...]) -> bool:
    if result.truncated or result.row_count != 2 or len(result.rows) != 2:
        return False
    if {r.get("COUNTY_ID") for r in result.rows} != {c.geo_id for c in counties}:
        return False
    for row in result.rows:
        counts = [row.get(k) for k in ("REFERENCE_ROWS", "UNIQUE_BLOCKS", "MATCHED_ROWS", "TOTAL_VALUES", "RENTER_VALUES")]
        occupied, rented, share = (row.get(k) for k in ("OCCUPIED", "RENTED", "RENTER_SHARE"))
        if not all(_number(v) for v in [*counts, occupied, rented, share, row.get("INVALID_VALUES")]):
            return False
        if counts[0] <= 0 or any(n != counts[0] for n in counts) or row["INVALID_VALUES"] != 0:
            return False
        if occupied <= 0 or not 0 <= rented <= occupied or not 0 <= share <= 100:
            return False
        if not math.isclose(share, 100 * rented / occupied, rel_tol=1e-9, abs_tol=1e-9):
            return False
    return True


@dataclass(frozen=True)
class Offer:
    id: str
    recipe: Recipe
    counties: tuple[GeoCandidate, ...]

    @property
    def request(self) -> str:
        return "Compare renter share in " + " and ".join(c.name for c in self.counties) + "."


@dataclass
class _State:
    generation: str
    expires: float
    offer: Offer | None = None


_states: OrderedDict[str, _State] = OrderedDict()


def _prune() -> None:
    now = time.monotonic()
    for key in [k for k, state in _states.items() if state.expires <= now]:
        del _states[key]


def begin_turn(session_id: str) -> str:
    # Called on the event loop; mutations contain no await and are atomic within
    # this single-process deployment. Generation checks reject late publishers.
    _prune()
    _states.pop(session_id, None)
    generation = uuid4().hex
    _states[session_id] = _State(generation, time.monotonic() + OFFER_TTL_S)
    while len(_states) > MAX_SESSIONS:
        _states.popitem(last=False)
    return generation


def publish(session_id: str, generation: str, recipe: Recipe, counties: tuple[GeoCandidate, ...]) -> bool:
    _prune()
    state = _states.get(session_id)
    if not state or state.generation != generation:
        return False
    state.offer = Offer(uuid4().hex, recipe, counties)
    state.expires = time.monotonic() + OFFER_TTL_S
    return True


def public_offers(session_id: str, generation: str) -> list[dict]:
    _prune()
    state = _states.get(session_id)
    if not state or state.generation != generation or not state.offer:
        return []
    return [{"suggestion_id": state.offer.id, "action_id": "compare_renter_share", "label": LABEL, "explanation": EXPLANATION}]


def consume(session_id: str, suggestion_id: str) -> Offer | None:
    _prune()
    state = _states.get(session_id)
    if not state or not state.offer or state.offer.id != suggestion_id:
        return None
    offer = state.offer
    begin_turn(session_id)
    return offer


async def _observed_tool(name, args, spans, results, timeout):
    preview = json.dumps(args)
    yield ChatEvent(type=EventType.TOOL_START, data={"tool": name, "args_preview": preview})
    started = time.monotonic()
    result = None
    try:
        call = getattr(tools, name)
        result = await asyncio.wait_for(asyncio.to_thread(call, **args), timeout=max(0, timeout))
    except Exception:
        # A timed-out thread may finish its read-only query under the existing
        # database timeout. It cannot publish an offer or stream late results.
        logger.warning("Follow-up %s failed or exceeded its wait budget", name, exc_info=True)
    elapsed = int((time.monotonic() - started) * 1000)
    if result is None:
        summary = {"error": "The follow-up check failed or exceeded its time budget."}
    elif isinstance(result, QueryResult):
        summary = {"row_count": result.row_count, "columns": result.columns,
                   "first_row": result.rows[0] if result.rows else None, "truncated": result.truncated}
    else:
        summary = {"hits": len(result.hits), "top": [h.variable_id for h in result.hits[:5]],
                   "labels": [h.label[:60] for h in result.hits[:3]], "truncated": result.truncated}
    spans.append(TraceSpan(name=f"tool:{name}", latency_ms=elapsed, ok=result is not None, meta={"args_preview": preview, **summary}))
    yield ChatEvent(type=EventType.TOOL_END, data={"tool": name, "ok": result is not None, "elapsed_ms": elapsed, "summary": summary})
    results.append(result)


async def prepare_offer(session_id, generation, context, turn_start, spans):
    counties = context.counties()
    deadline = turn_start + TURN_DEADLINE_S
    if not counties or deadline - time.monotonic() < MIN_PREFLIGHT_TIME_S:
        return
    found = []
    async for event in _observed_tool("search_census_variables", {"query": "tenure", "limit": 10}, spans, found, deadline - time.monotonic()):
        yield event
    recipe = discover_recipe(found[0]) if found[0] is not None else None
    if recipe is None or deadline - time.monotonic() < MIN_PREFLIGHT_TIME_S:
        return
    checked = []
    async for event in _observed_tool("run_census_sql", {"sql": query_for(recipe, counties)}, spans, checked, deadline - time.monotonic()):
        yield event
    if checked[0] is not None and time.monotonic() < deadline and valid_result(checked[0], counties):
        publish(session_id, generation, recipe, counties)


async def action_turn(session_id: str, offer: Offer):
    start, started_at = time.monotonic(), datetime.now(timezone.utc)
    spans: list[TraceSpan] = []
    await asyncio.to_thread(append_message, session_id, ChatMessage(role="user", content=offer.request))
    checked = []
    async for event in _observed_tool("run_census_sql", {"sql": query_for(offer.recipe, offer.counties)}, spans, checked, TURN_DEADLINE_S):
        yield event
    ok = checked[0] is not None and valid_result(checked[0], offer.counties)
    if ok:
        rows = {r["COUNTY_ID"]: r for r in checked[0].rows}
        lines = ["Renter share of occupied homes, ACS 2016-2020 five-year estimates:"]
        for county in offer.counties:
            row = rows[county.geo_id]
            lines.append(f"{county.name}: {row['RENTER_SHARE']:.1f}% ({row['RENTED']:,.0f} rented out of {row['OCCUPIED']:,.0f} occupied homes).")
        lines.append("Computed by summing block-group counts within each county, then dividing rented homes by occupied homes. This is a descriptive comparison, not a test of statistical significance.")
        answer = "\n\n".join(lines)
    else:
        answer = "I couldn't verify renter share for both counties with complete data. Please ask the housing question again to get a new suggestion."
    await asyncio.to_thread(append_message, session_id, ChatMessage(role="assistant", content=answer))
    elapsed = int((time.monotonic() - start) * 1000)
    await asyncio.to_thread(record_turn_trace, TurnTrace(session_id=session_id, user_message=offer.request, final_answer=answer, terminal_status="done" if ok else "error", started_at=started_at, total_ms=elapsed, spans=spans))
    yield ChatEvent(type=EventType.TOKEN, data={"text": answer})
    yield ChatEvent(type=EventType.DONE if ok else EventType.ERROR, data={"elapsed_ms": elapsed} if ok else {"message": answer})
