"""NBA day-slate scheduler: reload today's ET games every slate_refresh_seconds, poll each
book every nba_odds_poll_seconds while a slate game is still pregame."""

from datetime import date

from zoneinfo import ZoneInfo

from oddsapi.adapters import ADAPTERS
from oddsapi.config import Settings
from oddsapi.scores.espn import parse_scoreboard
from oddsapi.worker import Worker

from .conftest import load_fixture
from .test_http_and_worker import FakeAdapter
from .test_new_books import RECORDED  # Sunday 2026-11-22 21:43 ET

_ET = ZoneInfo("America/New_York")


class SlateScores:
    def __init__(self):
        self.calls = []

    def fetch(self, league, start, end):
        if start == date(2026, 11, 22):  # slate fetches start today; live-score polls start yesterday
            self.calls.append((league, start, end))
        games = parse_scoreboard(league, load_fixture("espn_nba_slate.json"), RECORDED)
        return [g for g in games if start <= g.starts_at.astimezone(_ET).date() <= end]


def make(conn, **kw):
    settings = Settings(db_path=":memory:", enabled_leagues=("nba",), **kw)
    games = ADAPTERS["bovada"](None, settings).parse("nba", load_fixture("bovada_nba.json"), RECORDED)
    book = FakeAdapter("bovada", games)
    scores = SlateScores()
    clock = [RECORDED.timestamp()]
    w = Worker(settings, conn, {"bovada": book}, scores, clock=lambda: clock[0])
    w.last_run["schedule:nba"] = clock[0]
    return w, book, scores, clock


def test_today_slate_already_started_means_no_odds_polls(conn):
    w, book, scores, _ = make(conn, slate_days=1)
    w.tick()
    assert scores.calls == [("nba", date(2026, 11, 22), date(2026, 11, 22))]
    assert w.slate_pregame("nba", RECORDED) == [] and book.calls == []


def test_defaults_refresh_and_poll_every_30_minutes(conn):
    w, book, scores, clock = make(conn, slate_days=2)
    assert w.settings.slate_refresh_seconds == 1800 and w.settings.odds_poll_for("nba") == 1800
    w.tick()
    assert scores.calls == [("nba", date(2026, 11, 22), date(2026, 11, 23))]
    assert len(w.slate_pregame("nba", RECORDED)) == 10
    assert book.calls == ["nba"]
    for _ in range(29):
        clock[0] += 60
        w.tick()
    assert book.calls == ["nba"] and len(scores.calls) == 1
    clock[0] += 60
    w.tick()
    assert book.calls == ["nba", "nba"] and len(scores.calls) == 2
    stored = conn.execute("SELECT COUNT(DISTINCT game_id) FROM prices WHERE book_id = 'bovada'").fetchone()[0]
    assert stored == 6
    assert conn.execute("SELECT COUNT(*) FROM price_snapshots WHERE book_id = 'bovada'").fetchone()[0] == 6 * 6


def test_intervals_are_configurable(conn):
    w, book, scores, clock = make(conn, slate_days=2, slate_refresh_seconds=600, nba_odds_poll_seconds=900)
    for _ in range(31):
        w.tick()
        clock[0] += 60
    assert len(scores.calls) == 4  # 0, 10, 20, 30 min
    assert len(book.calls) == 3  # 0, 15, 30 min


def test_poll_floor_still_applies(conn):
    w, *_ = make(conn, nba_odds_poll_seconds=5)
    assert w.settings.odds_poll_for("nba") == 60
