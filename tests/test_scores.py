from oddsapi.scores.espn import parse_scoreboard

from .conftest import RECORDED_AT, load_fixture


def games(league, name=None):
    return {g.external_id: g for g in parse_scoreboard(league, load_fixture(name or f"espn_{league}.json"), RECORDED_AT)}


def test_nba_statuses_and_periods():
    g = games("nba")
    final = g["401910029"]
    assert (final.status, final.home_score, final.away_score, final.period_detail, final.clock) == ("final", 107, 97, "final", None)
    live = g["401910030"]
    assert (live.status, live.period, live.period_detail, live.clock) == ("live", 4, "q4", "2:03")
    half = g["401910031"]
    assert (half.status, half.period_detail, half.clock) == ("live", "q2", "0:00")
    assert final.season == "2026"  # ESPN says 2027 for the 2026-27 NBA season
    assert final.venue == "State Farm Arena"
    assert final.linescore is None  # linescore is MLB only


def test_scheduled_games_have_no_score():
    sched = [x for x in games("nba", "espn_nba_schedule.json").values() if x.status == "scheduled"]
    assert sched
    assert all(x.home_score is None and x.period is None and x.period_detail is None for x in sched)


def test_mlb_linescore_and_final():
    g = games("mlb")["401908046"]
    assert g.status == "final" and g.clock is None
    assert (g.home.abbreviation, g.home_score, g.away_score) == ("TB", 1, 4)
    assert len(g.linescore) == 9
    assert g.linescore[1] == {"inning": 2, "away": 1, "home": 0}


def test_mlb_inning_halves():
    raw = load_fixture("espn_mlb.json")
    ev = raw["events"][0]
    ev["competitions"][0]["status"] = {"period": 5, "displayClock": "0:00", "type": {"name": "STATUS_IN_PROGRESS", "state": "in", "shortDetail": "Bot 5th"}}
    g = parse_scoreboard("mlb", raw, RECORDED_AT)[0]
    assert (g.status, g.period, g.period_detail, g.clock) == ("live", 5, "bottom_5", None)


def test_postponed_and_canceled():
    raw = load_fixture("espn_nfl.json")
    raw["events"][0]["competitions"][0]["status"]["type"].update(name="STATUS_POSTPONED", state="post")
    raw["events"][1]["competitions"][0]["status"]["type"].update(name="STATUS_CANCELED", state="post")
    out = parse_scoreboard("nfl", raw, RECORDED_AT)
    assert out[0].status == "postponed" and out[0].home_score is None
    assert out[1].status == "canceled"
