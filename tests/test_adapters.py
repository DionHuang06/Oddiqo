import pytest

from oddsapi.adapters import DraftKingsAdapter, FanDuelAdapter, OddsAdapter, PinnacleAdapter
from oddsapi.odds_math import american_to_decimal, parse_american

from .conftest import RECORDED_AT, load_fixture


def by_market(game):
    return {m.market: {o.side: (o.line, o.price_american) for o in m.outcomes} for m in game.markets}


def find(games, home, away):
    return next(g for g in games if g.home_name == home and g.away_name == away)


@pytest.mark.parametrize(
    "american,decimal", [(-110, "1.91"), (160, "2.60"), (-192, "1.52"), (100, "2.00"), (-100, "2.00"), (265, "3.65"), (-330, "1.30")]
)
def test_american_to_decimal(american, decimal):
    assert american_to_decimal(american) == decimal


def test_parse_american_display_strings():
    assert parse_american("\u2212110") == -110
    assert parse_american("+140") == 140
    assert parse_american("EVEN") == 100
    with pytest.raises(ValueError):
        american_to_decimal(50)


def test_all_adapters_share_interface(settings):
    for cls in (FanDuelAdapter, DraftKingsAdapter, PinnacleAdapter):
        a = cls(None, settings)
        assert isinstance(a, OddsAdapter)
        assert set(a.league_ids) == {"nba", "nfl", "mlb"}


class TestFanDuel:
    def test_nba_game_markets(self, settings):
        games = FanDuelAdapter(None, settings).parse("nba", load_fixture("fanduel_nba.json"), RECORDED_AT)
        g = find(games, "Golden State Warriors", "Boston Celtics")
        assert g.external_id == "36341635"
        assert g.starts_at.isoformat() == "2026-11-23T01:40:00+00:00"
        assert by_market(g) == {
            "moneyline": {"away": (None, -330), "home": (None, 265)},
            "spread": {"away": (-8.5, -106), "home": (8.5, -114)},
            "total": {"over": (211.5, -110), "under": (211.5, -110)},
        }

    def test_skips_futures_props_and_esports(self, settings):
        games = FanDuelAdapter(None, settings).parse("nba", load_fixture("fanduel_nba.json"), RECORDED_AT)
        assert len(games) == 6
        assert not any("(" in g.home_name for g in games)
        assert all({m.market for m in g.markets} <= {"moneyline", "spread", "total"} for g in games)

    def test_nfl(self, settings):
        games = FanDuelAdapter(None, settings).parse("nfl", load_fixture("fanduel_nfl.json"), RECORDED_AT)
        assert len(games) == 18
        assert all(len(g.markets) == 3 for g in games)

    def test_mlb_offseason_is_empty(self, settings):
        assert FanDuelAdapter(None, settings).parse("mlb", load_fixture("fanduel_mlb.json"), RECORDED_AT) == []


class TestPinnacle:
    def test_nba_pregame_only(self, settings):
        games = PinnacleAdapter(None, settings).parse("nba", load_fixture("pinnacle_nba.json"), RECORDED_AT)
        # Live matchups (children with a parent / isLive) and the market-less game are excluded.
        assert [(g.away_name, g.home_name) for g in games] == [("Boston Celtics", "Golden State Warriors")]
        assert by_market(games[0]) == {
            "moneyline": {"home": (None, 282), "away": (None, -320)},
            "spread": {"home": (8.0, -102), "away": (-8.0, -108)},
            "total": {"over": (211.5, -109), "under": (211.5, -103)},
        }

    def test_nfl_main_lines_not_alternates(self, settings):
        games = PinnacleAdapter(None, settings).parse("nfl", load_fixture("pinnacle_nfl.json"), RECORDED_AT)
        assert len(games) == 7
        for g in games:
            assert len({m.market for m in g.markets}) == len(g.markets)
            spread = next((m for m in g.markets if m.market == "spread"), None)
            if spread:
                lines = {o.side: o.line for o in spread.outcomes}
                assert lines["home"] == -lines["away"]

    def test_mlb_offseason_is_empty(self, settings):
        assert PinnacleAdapter(None, settings).parse("mlb", load_fixture("pinnacle_mlb.json"), RECORDED_AT) == []


class TestDraftKings:
    def test_nba_game_markets(self, settings):
        raw = load_fixture("draftkings_nba.json")
        games = DraftKingsAdapter(None, settings).parse("nba", raw, RECORDED_AT)
        started = [e for e in raw["events"] if e.get("status") == "STARTED"]
        assert started and len(games) == len(raw["events"]) - len(started)
        g = find(games, "GS Warriors", "BOS Celtics")
        assert g.starts_at.isoformat() == "2026-11-23T01:45:00+00:00"  # ESPN says 01:30; books drift by minutes
        assert by_market(g) == {
            "moneyline": {"away": (None, -336), "home": (None, 270)},
            "spread": {"away": (-8.5, -106), "home": (8.5, -114)},
            "total": {"over": (211.5, -112), "under": (211.5, -108)},
        }

    def test_nfl(self, settings):
        games = DraftKingsAdapter(None, settings).parse("nfl", load_fixture("draftkings_nfl.json"), RECORDED_AT)
        assert len(games) == 17 and all(len(g.markets) == 3 for g in games)

    def test_mlb_futures_only_is_empty(self, settings):
        assert DraftKingsAdapter(None, settings).parse("mlb", load_fixture("draftkings_mlb.json"), RECORDED_AT) == []
