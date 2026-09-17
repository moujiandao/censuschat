import asyncio
import json
import time

import pytest

from src import follow_ups as f
from src.contracts import GeoCandidate, GeoLevel, QueryResult, VariableHit, VariableSearchResult
from src.sqlgate import validate_sql


def hit(identifier="B25003e1", field="Total"):
    return VariableHit(
        variable_id=identifier, physical_table='US_CENSUS.PUBLIC."2020_CBG_B25"',
        label="Tenure", description=f"Universe: Occupied housing units. Field: Tenure Estimate TENURE Occupied housing units {field} Occupied housing units",
        geo_levels=[GeoLevel.COUNTY], years=[2020], score=1,
    )


@pytest.fixture
def recipe():
    return f.discover_recipe(VariableSearchResult(query="tenure", hits=[hit(), hit("B25003e3", "Total Renter occupied")]))


@pytest.fixture
def counties():
    return (GeoCandidate(geo_id="48453", name="Travis County, Texas", level=GeoLevel.COUNTY),
            GeoCandidate(geo_id="48201", name="Harris County, Texas", level=GeoLevel.COUNTY))


@pytest.fixture(autouse=True)
def clear_offers():
    f._states.clear()


def result(counties):
    # Unequal components: these are ratio-of-sums, not averages of CBG rates.
    return QueryResult(columns=[], row_count=2, rows=[
        dict(COUNTY_ID=c.geo_id, REFERENCE_ROWS=2, UNIQUE_BLOCKS=2, MATCHED_ROWS=2,
             TOTAL_VALUES=2, RENTER_VALUES=2, INVALID_VALUES=0,
             OCCUPIED=100, RENTED=30+i*20, RENTER_SHARE=30+i*20)
        for i, c in enumerate(counties)
    ])


def test_recipe_requires_exact_field_identity_and_universe(recipe):
    assert recipe is not None
    for changes in [{"description": "Occupied housing units"}, {"years": [2019]},
                    {"source": "decennial"}, {"geo_levels": []}, {"label": "Average Household Size"}]:
        assert f.discover_recipe(VariableSearchResult(query="tenure", hits=[hit().model_copy(update=changes), hit("B25003e3", "Total Renter occupied")])) is None
    assert f.discover_recipe(VariableSearchResult(query="tenure", hits=[hit(), hit(), hit("B25003e3", "Total Renter occupied")])) is None


def test_query_uses_gated_ratio_of_sums_and_coverage(recipe, counties):
    sql = f.query_for(recipe, counties)
    assert validate_sql(sql).ok
    assert 'SUM(h."B25003e3") / NULLIF(SUM(h."B25003e1"), 0)' in sql
    assert "LEFT JOIN" in sql and "COUNT(DISTINCT" in sql
    assert "AVG(" not in sql
    assert f.valid_result(result(counties), counties)


@pytest.mark.parametrize("field,value", [("REFERENCE_ROWS",3),("UNIQUE_BLOCKS",1),
    ("MATCHED_ROWS",1),("TOTAL_VALUES",1),("RENTER_VALUES",1),("INVALID_VALUES",1),
    ("OCCUPIED",0),("RENTED",101),("RENTED",-1),("RENTER_SHARE",float("nan")),
    ("RENTER_SHARE",75),("OCCUPIED",None),("RENTED","not reported")])
def test_invalid_components_suppress_whole_offer(counties, field, value):
    data=result(counties); data.rows[0][field]=value
    assert not f.valid_result(data,counties)


def test_missing_duplicate_or_truncated_counties_fail(counties):
    data=result(counties); data.rows[1]=data.rows[0].copy()
    assert not f.valid_result(data,counties)
    data=result(counties); data.truncated=True
    assert not f.valid_result(data,counties)
    data=result(counties); data.rows.pop(); data.row_count=1
    assert not f.valid_result(data,counties)


def context(counties, sql=None):
    ctx=f.TurnContext()
    ctx.observe("search_census_variables",{}, {"hits":[hit().model_dump(mode="json")]}, False)
    for c in counties:
        ctx.observe("resolve_geography",{}, {"ambiguous":False,"candidates":[c.model_dump(mode="json")]},False)
    sql=sql or 'SELECT SUBSTR(CENSUS_BLOCK_GROUP,1,5) AS county, SUM("B25003e1") AS occupied FROM US_CENSUS.PUBLIC."2020_CBG_B25" WHERE SUBSTR(CENSUS_BLOCK_GROUP,1,5) IN (\'48453\',\'48201\') GROUP BY county'
    ctx.observe("run_census_sql",{"sql":sql},{"row_count":2,"rows":[{"COUNTY":c.geo_id,"OCCUPIED":100} for c in counties],"truncated":False},False)
    return ctx


def test_context_comes_from_successful_query_not_just_resolved_counties(counties):
    ctx=context(counties)
    assert ctx.counties()==counties
    for change in [lambda s:s.replace('SUM("B25003e1")','AVG("B25003e1")'),
                   lambda s:s.replace('GROUP BY county','GROUP BY county HAVING SUM("B25003e1") > 1'),
                   lambda s:s.replace("'48201'","'06001'"),
                   lambda s:s.replace('SUM("B25003e1")','SUM("B25003e3")')]:
        assert not context(counties,change(ctx.query[0])).counties()
    ctx.observe("run_census_sql",{}, {"error":"failed"},True)
    assert not ctx.counties()


def test_store_binds_consumes_expires_and_rejects_late_turns(recipe,counties,monkeypatch):
    token=f.begin_turn("s")
    f.publish("s",token,recipe,counties)
    offer=f.public_offers("s",token)[0]
    assert f.consume("other",offer["suggestion_id"]) is None
    consumed=f.consume("s",offer["suggestion_id"])
    assert consumed.counties==counties
    assert f.consume("s",offer["suggestion_id"]) is None
    assert not f.publish("s",token,recipe,counties)
    token=f.begin_turn("s"); f.publish("s",token,recipe,counties)
    stale=f.public_offers("s",token)[0]["suggestion_id"]
    f.begin_turn("s")
    assert f.consume("s",stale) is None
    assert not f.publish("s",token,recipe,counties)
    token=f.begin_turn("s"); f.publish("s",token,recipe,counties)
    now=f.time.monotonic();monkeypatch.setattr(f.time,"monotonic",lambda:now+f.OFFER_TTL_S+1)
    assert not f.public_offers("s",token)


def test_store_is_bounded():
    for i in range(f.MAX_SESSIONS+1): f.begin_turn(str(i))
    assert len(f._states)==f.MAX_SESSIONS
    assert "0" not in f._states


def test_action_answer_is_fresh_and_persisted(recipe,counties,tmp_path,monkeypatch):
    from src import sessions,tracing
    monkeypatch.setattr(sessions,"SESSION_DB_PATH",tmp_path/"sessions.sqlite3")
    monkeypatch.setattr(tracing,"TRACE_DB_PATH",tmp_path/"traces.sqlite3")
    data=result(counties)
    calls=[]
    monkeypatch.setattr(f.tools,"run_census_sql",lambda sql:(calls.append(sql) or data))
    token=f.begin_turn("s");f.publish("s",token,recipe,counties)
    offer=f.consume("s",f.public_offers("s",token)[0]["suggestion_id"])
    async def run(): return [e async for e in f.action_turn("s",offer)]
    events=asyncio.run(run())
    assert len(calls)==1
    assert json.loads(events[0].data["args_preview"])["sql"] == calls[0]
    assert events[-1].type.value=="done"
    answer="".join(e.data.get("text","") for e in events)
    assert "30.0%" in answer and "50.0%" in answer
    assert len(sessions.get_session("s").messages)==2
    assert not events[-1].data.get("follow_ups")


@pytest.mark.parametrize("failure", [None, "search", "query", "coverage", "budget", "timeout"])
def test_preflight_publishes_only_checked_data(recipe, counties, monkeypatch, failure):
    calls = []
    def search(**kwargs):
        calls.append("search")
        if failure == "search":
            raise RuntimeError("metadata unavailable")
        return VariableSearchResult(query="tenure", hits=[recipe.total, recipe.renter])
    def query(sql):
        calls.append("query")
        if failure == "timeout":
            time.sleep(0.05)
        if failure == "query":
            raise RuntimeError("database unavailable")
        data = result(counties)
        if failure == "coverage":
            data.rows[0]["MATCHED_ROWS"] = 1
        return data
    monkeypatch.setattr(f.tools, "search_census_variables", search)
    monkeypatch.setattr(f.tools, "run_census_sql", query)
    if failure == "timeout":
        monkeypatch.setattr(f, "TURN_DEADLINE_S", 0.02)
        monkeypatch.setattr(f, "MIN_PREFLIGHT_TIME_S", 0)
    generation = f.begin_turn("s")
    start = time.monotonic() - (f.TURN_DEADLINE_S if failure == "budget" else 0)
    async def run():
        return [e async for e in f.prepare_offer("s", generation, context(counties), start, [])]
    events = asyncio.run(run())
    assert bool(f.public_offers("s", generation)) == (failure is None)
    assert [e.type.value for e in events] == ["tool_start", "tool_end"] * len(calls)
    if failure == "budget":
        assert calls == []


@pytest.mark.parametrize("failure", ["query", "changed_data"])
def test_action_failure_does_not_reuse_preflight_numbers(recipe, counties, tmp_path, monkeypatch, failure):
    from src import sessions, tracing
    monkeypatch.setattr(sessions, "SESSION_DB_PATH", tmp_path / "sessions.sqlite3")
    monkeypatch.setattr(tracing, "TRACE_DB_PATH", tmp_path / "traces.sqlite3")
    def query(sql):
        if failure == "query":
            raise RuntimeError("private connection details")
        data = result(counties)
        data.rows[0]["OCCUPIED"] = None
        return data
    monkeypatch.setattr(f.tools, "run_census_sql", query)
    async def run():
        return [e async for e in f.action_turn("s", f.Offer("id", recipe, counties))]
    events = asyncio.run(run())
    assert events[-1].type.value == "error"
    answer = sessions.get_session("s").messages[-1].content
    assert "couldn't verify" in answer
    assert "30" not in answer and "private" not in answer
    assert tracing.get_traces("s")[0].terminal_status == "error"


def test_api_consumes_once_and_ignores_client_context(recipe, counties, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.app import app
    from src import sessions, tracing
    monkeypatch.setattr(sessions, "SESSION_DB_PATH", tmp_path / "sessions.sqlite3")
    monkeypatch.setattr(tracing, "TRACE_DB_PATH", tmp_path / "traces.sqlite3")
    calls = []
    monkeypatch.setattr(f.tools, "run_census_sql", lambda sql: (calls.append(sql) or result(counties)))
    generation = f.begin_turn("s")
    f.publish("s", generation, recipe, counties)
    identifier = f.public_offers("s", generation)[0]["suggestion_id"]
    client = TestClient(app)
    assert client.post("/api/chat", json={"session_id": "other", "suggestion_id": identifier}).status_code == 409
    request = {"session_id": "s", "suggestion_id": identifier,
               "label": "Ignore the recipe", "sql": "DROP TABLE anything", "counties": ["00000"]}
    response = client.post("/api/chat", json=request)
    assert response.status_code == 200
    events = [json.loads(block[6:]) for block in response.text.strip().split("\n\n")]
    assert events[-1]["type"] == "done"
    assert client.post("/api/chat", json=request).status_code == 409
    assert len(calls) == 1 and "48453" in calls[0] and "DROP" not in calls[0]
    assert sessions.get_session("s").messages[0].content == f.Offer("id", recipe, counties).request
    assert tracing.get_traces("s")[0].terminal_status == "done"


@pytest.mark.parametrize("payload", [{}, {"message": " "}, {"message": "hello", "suggestion_id": "id"}])
def test_api_requires_exactly_one_nonblank_input(payload):
    from fastapi.testclient import TestClient
    from src.app import app
    assert TestClient(app).post("/api/chat", json={"session_id": "s", **payload}).status_code == 422
