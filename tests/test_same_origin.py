"""Cross-site write guard (D-034).

Caddy asks for the basic-auth login only on non-GET/HEAD/OPTIONS methods.
A browser that has saved that login attaches it to requests other websites
trigger too, so a page elsewhere could make a signed-in visitor's browser
POST /api/chat and spend model tokens. The guard refuses such writes before
any route runs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.same_origin import is_cross_site_write

HOST = "censuschat.brianmar.com"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
def test_reads_always_allowed(method):
    headers = {"sec-fetch-site": "cross-site", "origin": "https://evil.example", "host": HOST}
    assert not is_cross_site_write(method, headers)


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_sec_fetch_site_allows_same_origin_and_user_initiated(site):
    assert not is_cross_site_write("POST", {"sec-fetch-site": site, "host": HOST})


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_sec_fetch_site_refuses_everything_else(site):
    # same-site is refused too: a sibling *.brianmar.com app is a different
    # origin and must not be able to spend this app's tokens.
    assert is_cross_site_write("POST", {"sec-fetch-site": site, "host": HOST})


def test_sec_fetch_site_wins_over_origin():
    headers = {"sec-fetch-site": "cross-site", "origin": f"https://{HOST}", "host": HOST}
    assert is_cross_site_write("POST", headers)


def test_origin_matching_host_allowed():
    assert not is_cross_site_write("POST", {"origin": f"https://{HOST}", "host": HOST})


def test_origin_with_port_must_match_host_with_port():
    assert not is_cross_site_write(
        "POST", {"origin": "http://localhost:8000", "host": "localhost:8000"}
    )
    assert is_cross_site_write(
        "POST", {"origin": "http://localhost:3000", "host": "localhost:8000"}
    )


def test_origin_host_comparison_ignores_case():
    assert not is_cross_site_write("POST", {"origin": "https://CensusChat.brianmar.com", "host": HOST})


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "not a url"])
def test_foreign_or_opaque_origin_refused(origin):
    assert is_cross_site_write("DELETE", {"origin": origin, "host": HOST})


def test_origin_without_host_refused():
    assert is_cross_site_write("POST", {"origin": f"https://{HOST}"})


def test_no_browser_headers_allowed():
    # curl, scripts, the deploy probe: nothing a browser would send on a
    # cross-site request, so there is no saved login to abuse.
    assert not is_cross_site_write("POST", {"host": HOST})


# --- wired into the app -------------------------------------------------


client = TestClient(app)


def test_cross_site_chat_post_refused_before_the_route_runs(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("agent_turn must not run for a cross-site write")

    monkeypatch.setattr("src.app.agent_turn", fail)
    response = client.post(
        "/api/chat",
        json={"session_id": "s1", "message": "population of Wyoming?"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-site request refused."}


def test_same_origin_chat_post_reaches_the_route():
    # Empty body: a same-origin write gets past the guard and fails
    # validation in the route, so nothing is written or billed.
    response = client.post(
        "/api/chat", json={}, headers={"Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 422


def test_cross_site_get_still_served():
    response = client.get("/api/evals", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path", ["/", "/api/health", "/api/evals", "/api/trace-sessions", "/api/traces"]
)
def test_read_routes_refuse_writes(path):
    # The Caddy gate leaves GET open, so model-calling and data-changing
    # work must never answer it. The inverse holds as well: no read route
    # accepts a write method by accident.
    assert client.post(path).status_code == 405


def test_chat_does_not_answer_get():
    assert client.get("/api/chat").status_code == 405


def test_route_inventory_is_what_the_caddy_gate_assumes():
    # D-034 inventory. A new write route, or a WebSocket (the upgrade is a
    # GET, so Caddy would let it through without the login), fails here so
    # the gate's assumptions are re-checked rather than silently widened.
    from fastapi.routing import APIRoute, APIWebSocketRoute

    assert not [r for r in app.routes if isinstance(r, APIWebSocketRoute)]
    writes = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"GET", "HEAD", "OPTIONS"}
    }
    assert writes == {("POST", "/api/chat")}
