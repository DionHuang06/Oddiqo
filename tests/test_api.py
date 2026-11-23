from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from oddsapi.api import create_app
from oddsapi.auth import RateLimiter, create_key
from oddsapi.config import BOOKS, Settings

from .conftest import RECORDED_AT


@pytest.fixture
def now():
    return [RECORDED_AT + timedelta(seconds=30)]


@pytest.fixture
def client(loaded, settings, now):
    app = create_app(settings, loaded, RateLimiter(clock=lambda: now[0].timestamp()), clock=lambda: now[0])
    return TestClient(app)


@pytest.fixture
def key(loaded):
    return create_key(loaded, "test", "developer", ["scores:read", "odds:read"])


def h(k):
    return {"Authorization": f"Bearer {k}"}


def gsw_game_id(conn):
    return conn.execute("SELECT id FROM games WHERE home_team_id = 't_nba_gs' AND away_team_id = 't_nba_bos'").fetchone()["id"]


def test_requires_bearer_key(client, loaded):
    r = client.get("/v1/leagues")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.get("/v1/leagues", headers=h("sk_live_nope")).status_code == 401
    raw = create_key(loaded, "gone", "developer", ["scores:read"])
    loaded.execute("UPDATE api_keys SET revoked_at = '2026-01-01T00:00:00Z' WHERE developer_name = 'gone'")
    assert client.get("/v1/leagues", headers=h(raw)).status_code == 401


def test_leagues_and_books(client, key):
    assert client.get("/v1/leagues", headers=h(key)).json() == {
        "data": [
            {"id": "nba", "name": "NBA", "sport": "basketball"},
            {"id": "nfl", "name": "NFL", "sport": "american_football"},
            {"id": "mlb", "name": "MLB", "sport": "baseball"},
        ]
    }
    assert [b["id"] for b in client.get("/v1/books", headers=h(key)).json()["data"]] == list(BOOKS)


def test_games_for_a_day(client, key):
    r = client.get("/v1/games", params={"league": "nba", "on": "2026-11-22"}, headers=h(key))
    assert r.status_code == 200
    body = r.json()
    assert body["meta"] == {"count": 7, "league": "nba", "on": "2026-11-22", "time_zone": "America/New_York"}
    by_home = {g["home"]["short_name"]: g for g in body["data"]}
    assert by_home["MIA"]["status"] == "live"
    assert by_home["MIA"]["score"] == {
        "home": 121, "away": 108, "period": 4, "period_detail": "q4", "clock": "2:03",
        "captured_at": "2026-11-23T01:17:30Z", "stale": False,
    }
    assert by_home["ATL"]["score"]["period_detail"] == "final" and by_home["ATL"]["score"]["clock"] is None
    assert by_home["GS"]["score"] is None and by_home["GS"]["status"] == "scheduled"
    assert by_home["GS"]["home"] == {"id": "t_nba_gs", "name": "Golden State Warriors", "short_name": "GS"}

    live = client.get("/v1/games", params={"league": "nba", "on": "2026-11-22", "status": "live"}, headers=h(key)).json()
    assert {g["status"] for g in live["data"]} == {"live"}
    utc = client.get("/v1/games", params={"league": "nba", "on": "2026-11-23", "time_zone": "UTC"}, headers=h(key)).json()
    # The UTC day only picks up the Nov 22 (Eastern) games that tipped at or after 00:00Z.
    assert utc["meta"]["count"] == 5


@pytest.mark.parametrize(
    "params",
    [{}, {"league": "nba"}, {"league": "nhl", "on": "2026-11-22"}, {"league": "nba", "on": "11/22/2026"},
     {"league": "nba", "on": "2026-11-22", "time_zone": "Mars/Base"}, {"league": "nba", "on": "2026-11-22", "status": "halftime"}],
)
def test_games_bad_requests(client, key, params):
    r = client.get("/v1/games", params=params, headers=h(key))
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_game_detail_and_404(client, key, loaded):
    gid = gsw_game_id(loaded)
    d = client.get(f"/v1/games/{gid}", headers=h(key)).json()["data"]
    assert (d["season"], d["venue"], d["status"], d["score"]) == ("2026", "Chase Center", "scheduled", None)
    r = client.get("/v1/games/00000000-0000-0000-0000-000000000000", headers=h(key))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_mlb_detail_has_linescore(client, key, loaded):
    gid = loaded.execute("SELECT id FROM games WHERE league_id = 'mlb'").fetchone()["id"]
    score = client.get(f"/v1/games/{gid}", headers=h(key)).json()["data"]["score"]
    assert score["period_detail"] == "final" and len(score["linescore"]) == 9


def test_game_odds_shape(client, key, loaded):
    gid = gsw_game_id(loaded)
    data = client.get(f"/v1/games/{gid}/odds", headers=h(key)).json()["data"]
    assert data["game_id"] == gid
    assert [b["id"] for b in data["books"]] == ["fanduel", "draftkings", "pinnacle"]
    fd = data["books"][0]["markets"]
    assert [m["id"] for m in fd] == ["moneyline", "spread", "total"]
    assert fd[0]["outcomes"] == [
        {"side": "t_nba_bos", "price_american": -330, "price_decimal": "1.30"},
        {"side": "t_nba_gs", "price_american": 265, "price_decimal": "3.65"},
    ]
    assert fd[1]["outcomes"] == [
        {"side": "t_nba_bos", "line": -8.5, "price_american": -106, "price_decimal": "1.94"},
        {"side": "t_nba_gs", "line": 8.5, "price_american": -114, "price_decimal": "1.88"},
    ]
    assert fd[2]["outcomes"][0] == {"side": "over", "line": 211.5, "price_american": -110, "price_decimal": "1.91"}
    assert fd[0]["captured_at"] == "2026-11-23T01:17:30Z" and fd[0]["stale"] is False

    only = client.get(f"/v1/games/{gid}/odds", params={"books": "pinnacle", "markets": "spread"}, headers=h(key)).json()["data"]
    assert [(b["id"], [m["id"] for m in b["markets"]]) for b in only["books"]] == [("pinnacle", ["spread"])]
    assert client.get(f"/v1/games/{gid}/odds", params={"books": "bet365"}, headers=h(key)).status_code == 400
    assert client.get(f"/v1/games/{gid}/odds", params={"markets": "props"}, headers=h(key)).status_code == 400


def test_game_without_odds_has_empty_books(client, key, loaded):
    gid = loaded.execute("SELECT id FROM games WHERE league_id = 'mlb'").fetchone()["id"]
    assert client.get(f"/v1/games/{gid}/odds", headers=h(key)).json()["data"]["books"] == []


def test_stale_then_omitted(client, key, loaded, now):
    # NBA is polled every 30 minutes, so a price goes stale after 45 and is dropped after 75.
    gid = gsw_game_id(loaded)
    odds = lambda: client.get(f"/v1/games/{gid}/odds", headers=h(key)).json()["data"]["books"]  # noqa: E731
    now[0] = RECORDED_AT + timedelta(seconds=300)
    assert odds()[0]["markets"][0]["stale"] is False
    now[0] = RECORDED_AT + timedelta(seconds=2701)
    assert odds()[0]["markets"][0]["stale"] is True
    now[0] = RECORDED_AT + timedelta(seconds=4501)
    assert odds() == []


def test_ui_is_served_without_data(client):
    r = client.get("/ui")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "/v1/odds" in r.text and "Bearer" in r.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui"


def test_stale_then_omitted_on_fast_schedule(loaded, now):
    settings = Settings(db_path=":memory:", slate_leagues=())
    c = TestClient(create_app(settings, loaded, RateLimiter(clock=lambda: now[0].timestamp()), clock=lambda: now[0]))
    k = create_key(loaded, "fast", "developer", ["odds:read"])
    gid = gsw_game_id(loaded)
    now[0] = RECORDED_AT + timedelta(seconds=300)
    assert c.get(f"/v1/games/{gid}/odds", headers=h(k)).json()["data"]["books"][0]["markets"][0]["stale"] is True
    now[0] = RECORDED_AT + timedelta(seconds=601)
    assert c.get(f"/v1/games/{gid}/odds", headers=h(k)).json()["data"]["books"] == []


def test_board(client, key):
    body = client.get("/v1/odds", params={"league": "nfl", "on": "2026-11-29"}, headers=h(key)).json()
    assert body["meta"]["count"] == len(body["data"]) > 0
    g = body["data"][0]
    assert set(g) == {"id", "league", "starts_at", "status", "home", "away", "books"}
    assert any(b["id"] == "fanduel" for x in body["data"] for b in x["books"])
    assert client.get("/v1/odds", params={"league": "nfl"}, headers=h(key)).status_code == 400


def test_scopes_and_sandbox_league(client, loaded):
    scores_only = create_key(loaded, "s", "developer", ["scores:read"])
    r = client.get("/v1/odds", params={"league": "nba", "on": "2026-11-22"}, headers=h(scores_only))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    sandbox = create_key(loaded, "sb", "sandbox", ["scores:read", "odds:read"], league="nfl")
    assert client.get("/v1/games", params={"league": "nba", "on": "2026-11-22"}, headers=h(sandbox)).status_code == 403
    assert client.get("/v1/games", params={"league": "nfl", "on": "2026-11-22"}, headers=h(sandbox)).status_code == 200
    assert client.get(f"/v1/games/{gsw_game_id(loaded)}", headers=h(sandbox)).status_code == 403


def test_rate_limit_headers_and_429(client, loaded):
    k = create_key(loaded, "sb", "sandbox", ["scores:read"], league="nba")
    r = client.get("/v1/leagues", headers=h(k))
    assert r.headers["X-RateLimit-Limit"] == "60" and r.headers["X-RateLimit-Remaining"] == "59"
    assert int(r.headers["X-RateLimit-Reset"]) > RECORDED_AT.timestamp()
    for _ in range(59):
        client.get("/v1/leagues", headers=h(k))
    r = client.get("/v1/leagues", headers=h(k))
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) >= 1 and r.headers["X-RateLimit-Remaining"] == "0"


def test_rate_headers_on_404(client, key):
    r = client.get("/v1/games/nope", headers=h(key))
    assert r.status_code == 404 and "X-RateLimit-Limit" in r.headers
