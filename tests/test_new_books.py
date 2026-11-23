"""The seven books added for the NBA slate, parsed from fixtures recorded at 2026-11-23T02:43:58Z
(Sunday 9:43 PM ET: Sunday's late games live, Monday's slate pregame)."""

import json

import pytest

from oddsapi.adapters import ADAPTERS, OddsAdapter
from oddsapi.config import BOOKS
from oddsapi.db import from_iso
from oddsapi.ingest import ingest_book_games, upsert_score_games
from oddsapi.odds_math import decimal_to_american, prob_to_american
from oddsapi.scores.espn import parse_scoreboard

from .conftest import FIXTURES, load_fixture

NEW_BOOKS = ("betrivers", "bovada", "unibet", "kalshi", "polymarket", "matchbook", "smarkets")
RECORDED = from_iso(json.loads((FIXTURES / "bovada_nba.json").read_text())["_recorded_at"])


def parse(book, settings):
    a = ADAPTERS[book](None, settings)
    return a, a.parse("nba", load_fixture(f"{book}_nba.json"), RECORDED)


def por_phi(games):
    return next(g for g in games if g.home_name in ("PHI 76ers", "Philadelphia 76ers", "76ers", "PHI"))


def by_market(game):
    return {m.market: {o.side: (o.line, o.price_american) for o in m.outcomes} for m in game.markets}


@pytest.fixture
def slate(conn):
    upsert_score_games(conn, parse_scoreboard("nba", load_fixture("espn_nba_slate.json"), RECORDED))
    return conn


def test_ten_books_registered():
    assert len(BOOKS) == 10 and set(BOOKS) == set(ADAPTERS)


@pytest.mark.parametrize("book", NEW_BOOKS)
def test_shared_interface_and_shape(book, settings):
    a, games = parse(book, settings)
    assert isinstance(a, OddsAdapter) and a.supports("nba") and not a.supports("nfl")
    assert games
    for g in games:
        assert g.starts_at > RECORDED or g.date_only
        markets = [m.market for m in g.markets]
        assert len(markets) == len(set(markets)) and set(markets) <= {"moneyline", "spread", "total"}
        for m in g.markets:
            sides = {o.side for o in m.outcomes}
            assert sides == ({"over", "under"} if m.market == "total" else {"home", "away"})
            assert all(abs(o.price_american) >= 100 for o in m.outcomes)
            lines = {o.side: o.line for o in m.outcomes}
            if m.market == "spread":
                assert lines["home"] == -lines["away"]
            if m.market == "total":
                assert lines["over"] == lines["under"]


@pytest.mark.parametrize(
    "book,expected",
    [
        ("betrivers", {
            "moneyline": {"away": (None, 132), "home": (None, -165)},
            "spread": {"away": (3.5, -113), "home": (-3.5, -110)},
            "total": {"over": (229.5, -113), "under": (229.5, -110)},
        }),
        ("bovada", {
            "moneyline": {"away": (None, 130), "home": (None, -150)},
            "spread": {"away": (3.0, -110), "home": (-3.0, -110)},
            "total": {"over": (229.5, -110), "under": (229.5, -110)},
        }),
        ("unibet", {
            "moneyline": {"away": (None, 132), "home": (None, -162)},
            "spread": {"away": (3.5, -113), "home": (-3.5, -110)},
            "total": {"over": (229.5, -113), "under": (229.5, -110)},
        }),
        # Exchanges: ask for a $1 contract; POR yes at 42c is +138, PHI at 59c is -144.
        ("kalshi", {
            "moneyline": {"away": (None, 138), "home": (None, -144)},
            "spread": {"away": (3.5, -108), "home": (-3.5, 104)},
            "total": {"over": (229.5, -104), "under": (229.5, -104)},
        }),
        ("polymarket", {
            "moneyline": {"away": (None, 138), "home": (None, -144)},
            "spread": {"away": (3.5, -108), "home": (-3.5, 104)},
            "total": {"over": (229.5, -104), "under": (229.5, 100)},
        }),
        ("matchbook", {
            "moneyline": {"away": (None, 138), "home": (None, -139)},
            "spread": {"away": (3.0, -104), "home": (-3.0, -108)},
            "total": {"over": (229.5, -109), "under": (229.5, -105)},
        }),
        ("smarkets", {
            "moneyline": {"away": (None, 120), "home": (None, -167)},
            "spread": {"away": (3.0, -119), "home": (-3.0, -127)},
            "total": {"over": (229.0, -123), "under": (229.0, -122)},
        }),
    ],
)
def test_trail_blazers_at_76ers(book, expected, settings):
    _, games = parse(book, settings)
    g = por_phi(games)
    assert by_market(g) == expected


@pytest.mark.parametrize("book", NEW_BOOKS)
def test_every_listed_slate_game_matches(book, settings, slate):
    a, games = parse(book, settings)
    stats = ingest_book_games(slate, book, a.source, games, 15, RECORDED)
    assert stats["matched"] >= 6
    if book not in ("kalshi", "polymarket"):  # the exchanges also list games beyond the two-day slate
        assert stats["unmatched"] == []


def test_kalshi_date_only_skips_games_in_progress(settings, slate):
    a, games = parse("kalshi", settings)
    assert any(g.external_id == "2026-11-22-BOS-GSW" for g in games)  # listed, but ESPN has it at halftime
    ingest_book_games(slate, "kalshi", a.source, games, 15, RECORDED)
    rows = slate.execute(
        """SELECT DISTINCT g.status FROM prices p JOIN games g ON g.id = p.game_id WHERE p.book_id = 'kalshi'"""
    ).fetchall()
    assert [r["status"] for r in rows] == ["scheduled"]


def test_kalshi_maps_team_codes_to_espn(settings):
    _, games = parse("kalshi", settings)
    codes = {c for g in games for c in (g.home_name, g.away_name)}
    assert not codes & {"GSW", "NYK", "SAS", "NOP", "UTA", "WAS"}
    assert all(g.date_only for g in games)


def test_polymarket_ignores_non_game_events(settings):
    _, games = parse("polymarket", settings)
    assert not any("exact-margin" in g.external_id for g in games)


def test_exchanges_skip_empty_books_that_look_balanced(settings):
    """A 1c bid / 99c ask has a 50c midpoint but no real price; it must not become the 'main' line."""
    def mk(line, bid, ask):
        return {"sportsMarketType": "spreads", "line": line, "outcomes": '["76ers", "Trail Blazers"]',
                "bestBid": bid, "bestAsk": ask, "active": True, "closed": False, "acceptingOrders": True}

    raw = {"events": [{"slug": "nba-por-phi-2099-01-01", "title": "Trail Blazers vs. 76ers", "startTime": "2099-01-02T00:00:00Z",
                       "markets": [mk(-15.5, 0.01, 0.99), mk(-3.5, 0.44, 0.45)]}]}
    games = ADAPTERS["polymarket"](None, settings).parse("nba", raw, RECORDED)
    assert by_market(games[0])["spread"] == {"home": (-3.5, 122), "away": (3.5, -127)}

    km = lambda strike, bid, ask, no_ask: {"ticker": f"KXNBASPREAD-99JAN01PORPHI-PHI{strike}", "floor_strike": strike - 0.5,  # noqa: E731
                                          "yes_bid_dollars": bid, "yes_ask_dollars": ask, "no_ask_dollars": no_ask, "status": "active"}
    kraw = {"spread": [{"event_ticker": "KXNBASPREAD-99JAN01PORPHI", "markets": [km(16, "0.01", "0.99", "0.99"), km(4, "0.47", "0.49", "0.53")]}]}
    kg = ADAPTERS["kalshi"](None, settings).parse("nba", kraw, RECORDED)
    assert by_market(kg[0])["spread"] == {"home": (-3.5, 104), "away": (3.5, -113)}


def test_matchbook_nba_only(settings):
    raw = load_fixture("matchbook_nba.json")
    _, games = parse("matchbook", settings)
    non_nba = [e for e in raw["events"] if not any(t.get("name") == "NBA" for t in e["meta-tags"])]
    assert non_nba and not {g.external_id for g in games} & {str(e["id"]) for e in non_nba}


def test_unibet_uses_main_line_not_alternates(settings):
    raw = load_fixture("unibet_nba.json")
    alternates = [bo for off in raw["offers"].values() for bo in off["betOffers"] if "MAIN_LINE" not in (bo["tags"] or [])]
    assert alternates
    _, games = parse("unibet", settings)
    assert all(len(g.markets) <= 3 for g in games)


@pytest.mark.parametrize("p,american", [(0.5, 100), (0.42, 138), (0.59, -144), (0.25, 300), (0.8, -400), (0.005, None), (0.995, None)])
def test_prob_to_american(p, american):
    assert prob_to_american(p) == american


@pytest.mark.parametrize("dec,american", [(2.38, 138), (1.71, -141), (2.0, 100), (1.5, -200)])
def test_decimal_to_american(dec, american):
    assert decimal_to_american(dec) == american
