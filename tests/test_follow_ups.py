import pytest

from src import follow_ups as f
from src.contracts import GeoCandidate, GeoLevel, VariableHit


def hit(
    identifier: str,
    *,
    label: str,
    description: str,
    table: str,
) -> VariableHit:
    return VariableHit(
        variable_id=identifier,
        physical_table=f'US_CENSUS.PUBLIC."2020_CBG_{table}"',
        label=label,
        description=description,
        geo_levels=[GeoLevel.COUNTY],
        years=[2020],
        score=1,
    )


HOUSING = hit(
    "B25003e1",
    label="Tenure",
    description="Universe: Occupied housing units. Field: Tenure Estimate Total",
    table="B25",
)
RENTER = hit(
    "B25003e3",
    label="Tenure",
    description="Universe: Occupied housing units. Field: Tenure Estimate Total Renter occupied",
    table="B25",
)
POPULATION = hit(
    "B01003e1",
    label="Total population",
    description="Universe: Total population. Field: Total population Estimate Total",
    table="B01",
)
AGE_UNDER_5 = hit(
    "B01001e3",
    label="Sex By Age",
    description="Universe: Total population. Field: Sex By Age Estimate Male Under 5 years",
    table="B01",
)
AGE_5_TO_9 = hit(
    "B01001e4",
    label="Sex By Age",
    description="Universe: Total population. Field: Sex By Age Estimate Male 5 to 9 years",
    table="B01",
)
GENERIC = hit(
    "B08014e1",
    label="Vehicles available",
    description="Universe: Workers. Field: Vehicles available Estimate",
    table="B08",
)


@pytest.fixture
def counties():
    return (
        GeoCandidate(
            geo_id="48453", name="Travis County, Texas", level=GeoLevel.COUNTY
        ),
        GeoCandidate(
            geo_id="48201", name="Harris County, Texas", level=GeoLevel.COUNTY
        ),
    )


def context(
    counties,
    variables=(HOUSING, RENTER, POPULATION, AGE_UNDER_5, AGE_5_TO_9),
    *,
    rows=2,
):
    ctx = f.TurnContext()
    ctx.observe(
        "search_census_variables",
        {},
        {"hits": [variable.model_dump(mode="json") for variable in variables]},
        False,
    )
    for county in counties:
        ctx.observe(
            "resolve_geography",
            {},
            {
                "ambiguous": False,
                "candidates": [county.model_dump(mode="json")],
            },
            False,
        )
    selected = ", ".join(f'SUM("{variable.variable_id}")' for variable in variables)
    tables = list(dict.fromkeys(variable.physical_table for variable in variables))
    joined_tables = tables[0] + " " + " ".join(
        f"JOIN {table} USING (CENSUS_BLOCK_GROUP)" for table in tables[1:]
    )
    sql = (
        f"SELECT LEFT(CENSUS_BLOCK_GROUP, 5) AS COUNTY_ID, {selected} "
        f"FROM {joined_tables} WHERE LEFT(CENSUS_BLOCK_GROUP, 5) IN "
        f"({', '.join(repr(county.geo_id) for county in counties)}) "
        "GROUP BY LEFT(CENSUS_BLOCK_GROUP, 5)"
    )
    ctx.observe(
        "run_census_sql",
        {"sql": sql},
        {
            "row_count": rows,
            "rows": [{"COUNTY_ID": county.geo_id} for county in counties[:rows]],
            "truncated": False,
        },
        False,
    )
    return ctx


def test_supported_comparison_returns_three_distinct_contextual_questions(counties):
    questions = f.questions(context(counties))

    assert [question["id"] for question in questions] == [
        "compare_renter_share",
        "compare_another_county",
        "compare_age_distribution",
    ]
    assert len({question["question"] for question in questions}) == 3
    assert all("Travis County, Texas" in question["question"] for question in questions)
    assert all("Harris County, Texas" in question["question"] for question in questions)


def test_catalogue_returns_only_eligible_directions(counties):
    assert [question["id"] for question in f.questions(context(counties, (GENERIC,)))] == [
        "compare_another_county"
    ]
    assert [question["id"] for question in f.questions(context(counties, (HOUSING,)))] == [
        "compare_another_county",
    ]
    assert [
        question["id"]
        for question in f.questions(context(counties, (HOUSING, RENTER)))
    ] == [
        "compare_renter_share",
        "compare_another_county",
    ]
    assert [question["id"] for question in f.questions(context(counties, (POPULATION,)))] == [
        "compare_another_county",
    ]
    assert [
        question["id"]
        for question in f.questions(context(counties, (AGE_UNDER_5, AGE_5_TO_9)))
    ] == [
        "compare_another_county",
        "compare_age_distribution",
    ]


@pytest.mark.parametrize("condition", ["ambiguous", "failed", "empty", "truncated"])
def test_uncertain_or_failed_turns_return_no_questions(counties, condition):
    ctx = context(counties)
    if condition == "ambiguous":
        ctx.observe(
            "resolve_geography",
            {},
            {
                "ambiguous": True,
                "candidates": [county.model_dump(mode="json") for county in counties],
            },
            False,
        )
    elif condition == "failed":
        ctx.observe("run_census_sql", {}, {"error": "database unavailable"}, True)
    elif condition == "empty":
        ctx.observe(
            "run_census_sql", {"sql": "SELECT 1"}, {"row_count": 0, "rows": []}, False
        )
    else:
        ctx.observe(
            "run_census_sql",
            {"sql": "SELECT 1"},
            {"row_count": 1, "rows": [{"VALUE": 1}], "truncated": True},
            False,
        )

    assert f.questions(ctx) == []


def test_query_must_reference_discovered_metric_and_both_resolved_counties(counties):
    ctx = context(counties, (GENERIC,))
    assert f.questions(ctx)

    missing_place = context(counties, (GENERIC,))
    evidence = missing_place.successful_queries[0]
    missing_place.successful_queries[0] = f.QueryEvidence(
        evidence.sql.replace("48201", "06001"), evidence.row_count, evidence.rows
    )
    assert f.questions(missing_place) == []

    unknown_metric = context(counties, (GENERIC,))
    evidence = unknown_metric.successful_queries[0]
    unknown_metric.successful_queries[0] = f.QueryEvidence(
        evidence.sql.replace(GENERIC.variable_id, "B99999e1"),
        evidence.row_count,
        evidence.rows,
    )
    assert f.questions(unknown_metric) == []


def test_non_county_or_more_than_two_places_are_not_supported(counties):
    state = GeoCandidate(geo_id="48", name="Texas", level=GeoLevel.STATE)
    assert f.questions(context((counties[0], state), (GENERIC,))) == []

    third = GeoCandidate(
        geo_id="48029", name="Bexar County, Texas", level=GeoLevel.COUNTY
    )
    assert f.questions(context((*counties, third), (GENERIC,))) == []


def test_county_ids_must_be_filter_literals_not_variable_id_substrings():
    counties = (
        GeoCandidate(
            geo_id="25003", name="Berkshire County, Massachusetts", level=GeoLevel.COUNTY
        ),
        GeoCandidate(
            geo_id="25005", name="Bristol County, Massachusetts", level=GeoLevel.COUNTY
        ),
    )
    ctx = context(counties, (HOUSING, RENTER))
    ctx.successful_queries[0] = f.QueryEvidence(
        'SELECT LEFT(CENSUS_BLOCK_GROUP, 5) AS COUNTY_ID, SUM("B25003e1") '
        'FROM US_CENSUS.PUBLIC."2020_CBG_B25" '
        "WHERE CATEGORY IN ('25003', '25005') "
        "AND LEFT(CENSUS_BLOCK_GROUP, 5) IN ('25005', '25007') "
        "GROUP BY LEFT(CENSUS_BLOCK_GROUP, 5)",
        2,
        [{"COUNTY_ID": "25005"}, {"COUNTY_ID": "25007"}],
    )

    assert f.questions(ctx) == []


def test_returned_rows_must_match_both_resolved_counties(counties):
    ctx = context(counties, (GENERIC,))
    evidence = ctx.successful_queries[0]
    ctx.successful_queries[0] = f.QueryEvidence(
        evidence.sql,
        2,
        [{"COUNTY_ID": counties[0].geo_id}, {"COUNTY_ID": counties[0].geo_id}],
    )

    assert f.questions(ctx) == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": " "},
        {"message": "hello", "suggestion_id": "opaque-action"},
        {"suggestion_id": "opaque-action"},
    ],
)
def test_api_accepts_only_one_nonblank_message(payload):
    from fastapi.testclient import TestClient

    from src.app import app

    assert (
        TestClient(app).post("/api/chat", json={"session_id": "s", **payload}).status_code
        == 422
    )
