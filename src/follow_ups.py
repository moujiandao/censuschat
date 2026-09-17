"""Deterministic contextual questions derived from completed-turn evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from src.contracts import DEFAULT_VINTAGE, GeoCandidate, GeoLevel, VariableHit


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def _supported(hit: VariableHit) -> bool:
    return (
        hit.source == "acs"
        and DEFAULT_VINTAGE in hit.years
        and GeoLevel.COUNTY in hit.geo_levels
    )


def _county_prefix(node: exp.Expression) -> bool:
    column = node.this if isinstance(node, (exp.Left, exp.Substring)) else None
    if not isinstance(column, exp.Column) or column.name.upper() != "CENSUS_BLOCK_GROUP":
        return False
    if isinstance(node, exp.Left):
        return node.expression == exp.Literal.number(5)
    return (
        node.args.get("start") == exp.Literal.number(1)
        and node.args.get("length") == exp.Literal.number(5)
    )


@dataclass(frozen=True)
class QueryEvidence:
    sql: str
    row_count: int
    rows: list[dict[str, Any]]


@dataclass
class TurnContext:
    """Evidence observed on the normal agent path for one turn.

    Suggestions fail closed. They require two resolved counties, runtime
    metadata, a recognizable county comparison query, and no failed or
    ambiguous tool result anywhere in the turn.
    """

    variables: dict[str, VariableHit] = field(default_factory=dict)
    geographies: dict[tuple[GeoLevel, str], GeoCandidate] = field(
        default_factory=dict
    )
    successful_queries: list[QueryEvidence] = field(default_factory=list)
    failed: bool = False

    def observe(
        self, name: str, args: dict[str, Any], result: dict[str, Any], error: bool
    ) -> None:
        if error:
            self.failed = True
            return

        try:
            if name == "search_census_variables":
                for raw in result.get("hits", []):
                    hit = VariableHit.model_validate(raw)
                    if _supported(hit):
                        self.variables[hit.variable_id.upper()] = hit
            elif name == "resolve_geography":
                candidates = result.get("candidates", [])
                if result.get("ambiguous") or len(candidates) != 1:
                    self.failed = True
                    return
                geography = GeoCandidate.model_validate(candidates[0])
                self.geographies[(geography.level, geography.geo_id)] = geography
            elif name == "run_census_sql":
                rows = result.get("rows", [])
                row_count = result.get("row_count", len(rows))
                sql = args.get("sql")
                if (
                    result.get("truncated")
                    or row_count != 2
                    or len(rows) != 2
                    or not isinstance(sql, str)
                    or not sql.strip()
                ):
                    self.failed = True
                    return
                self.successful_queries.append(QueryEvidence(sql, row_count, rows))
        except (TypeError, ValueError, KeyError):
            self.failed = True


def _matched_variables(
    evidence: QueryEvidence,
    counties: tuple[GeoCandidate, GeoCandidate],
    variables: dict[str, VariableHit],
) -> list[VariableHit]:
    """Return discovered variables used by one proven county comparison."""

    try:
        tree = parse_one(evidence.sql, read="snowflake")
        if not isinstance(tree, exp.Select):
            return []

        prefixes = [
            item
            for item in tree.expressions
            if isinstance(item, exp.Alias) and _county_prefix(item.this)
        ]
        if len(prefixes) != 1:
            return []
        county_alias = prefixes[0].alias.upper()
        expected_ids = {county.geo_id for county in counties}

        where = tree.args.get("where")
        filters = (
            [
                node
                for node in where.find_all(exp.In)
                if _county_prefix(node.this) and not node.args.get("query")
            ]
            if where is not None
            else []
        )
        if len(filters) != 1:
            return []
        filter_ids = {
            item.this
            for item in filters[0].expressions
            if isinstance(item, exp.Literal) and item.is_string
        }
        if len(filters[0].expressions) != 2 or filter_ids != expected_ids:
            return []

        group = tree.args.get("group")
        if group is None or not any(
            _county_prefix(item)
            or (
                isinstance(item, exp.Column)
                and not item.table
                and item.name.upper() == county_alias
            )
            for item in group.expressions
        ):
            return []

        returned_ids = {
            {str(key).upper(): value for key, value in row.items()}.get(county_alias)
            for row in evidence.rows
        }
        if returned_ids != expected_ids:
            return []

        tables = {
            ".".join(
                part for part in (table.catalog, table.db, table.name) if part
            ).upper()
            for table in tree.find_all(exp.Table)
        }
        columns = {column.name.upper() for column in tree.find_all(exp.Column)}
        return [
            variable
            for identifier, variable in variables.items()
            if identifier in columns
            and variable.physical_table.replace('"', "").upper() in tables
        ]
    except (AttributeError, KeyError, SqlglotError, TypeError, ValueError):
        return []


def _renter_share_available(variables: list[VariableHit]) -> bool:
    tenure = [
        variable
        for variable in variables
        if _normalized(variable.label) == "tenure"
        and "universe: occupied housing units" in _normalized(variable.description)
    ]
    totals = [
        variable
        for variable in tenure
        if " total" in _normalized(variable.description)
        and "owner occupied" not in _normalized(variable.description)
        and "renter occupied" not in _normalized(variable.description)
    ]
    renters = [
        variable
        for variable in tenure
        if "renter occupied" in _normalized(variable.description)
    ]
    return (
        len(totals) == 1
        and len(renters) == 1
        and totals[0].physical_table == renters[0].physical_table
    )


def _age_distribution_available(variables: list[VariableHit]) -> bool:
    age_fields = [
        variable
        for variable in variables
        if _normalized(variable.label) == "sex by age"
        and "universe: total population" in _normalized(variable.description)
    ]
    return len(age_fields) >= 2 and len(
        {variable.physical_table for variable in age_fields}
    ) == 1


def _eligible_context(
    context: TurnContext,
) -> tuple[tuple[GeoCandidate, GeoCandidate], list[VariableHit]] | None:
    if context.failed or len(context.geographies) != 2:
        return None
    counties = tuple(context.geographies.values())
    if not all(county.level == GeoLevel.COUNTY for county in counties):
        return None

    matched = [
        variable
        for evidence in context.successful_queries
        for variable in _matched_variables(evidence, counties, context.variables)
    ]
    if not matched:
        return None
    return (counties[0], counties[1]), list(context.variables.values())


def questions(context: TurnContext) -> list[dict[str, str]]:
    """Return zero to three plain-text questions for the completed turn."""

    eligible = _eligible_context(context)
    if eligible is None:
        return []

    counties, available_variables = eligible
    places = f"{counties[0].name} and {counties[1].name}"
    selected: list[dict[str, str]] = []
    if _renter_share_available(available_variables):
        selected.append(
            {
                "id": "compare_renter_share",
                "question": f"Compare renter share in {places}.",
            }
        )
    selected.append(
        {
            "id": "compare_another_county",
            "question": f"Compare {places} with another county.",
        }
    )
    if _age_distribution_available(available_variables):
        selected.append(
            {
                "id": "compare_age_distribution",
                "question": f"Compare age distributions in {places}.",
            }
        )
    return selected[:3]
