from datetime import datetime, timedelta, timezone

import httpx
import pytest

from oddsapi.adapters import FanDuelAdapter, OddsAdapter
from oddsapi.config import Settings
from oddsapi.http import Backoff, BlockedError, PoliteClient, RateLimitedError, TransientError
from oddsapi.worker import Worker

from .conftest import RECORDED_AT, load_all, load_fixture


def client_with(handler, settings=None, sleeps=None):
    clock = [0.0]

    def sleep(s):
        (sleeps if sleeps is not None else []).append(s)
        clock[0] += s

    return PoliteClient(settings or Settings(), transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: clock[0])


def test_sends_clear_user_agent_and_no_cookies():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, json={"ok": 1})

    client_with(handler, Settings(user_agent="proto-bot/1.0 (contact: me)")).get_json("https://example.test/x")
    assert seen["user-agent"] == "proto-bot/1.0 (contact: me)"
    assert "cookie" not in seen


def test_host_gap_is_enforced():
    sleeps = []
    c = client_with(lambda r: httpx.Response(200, json={}), Settings(host_gap_seconds=3), sleeps)
    c.get_json("https://a.test/1")
    c.get_json("https://a.test/2")
    c.get_json("https://b.test/1")
    assert sleeps == [3]


@pytest.mark.parametrize(
    "status,headers,exc",
    [(403, {}, BlockedError), (401, {}, BlockedError), (429, {"Retry-After": "90"}, RateLimitedError), (503, {}, TransientError)],
)
def test_error_statuses(status, headers, exc):
    c = client_with(lambda r: httpx.Response(status, headers=headers, text="<html>Access Denied</html>"))
    with pytest.raises(exc) as info:
        c.get_json("https://sportsbook.test/api")
    if status == 429:
        assert info.value.retry_after == 90


def test_backoff_growth_and_caps():
    b = Backoff(base=120, cap=3600, blocked_base=6 * 3600, blocked_cap=24 * 3600)
    assert b.failure(TransientError("x"), 0) == 120
    assert b.failure(TransientError("x"), 0) == 240
    assert b.failure(RateLimitedError("x", retry_after=900), 0) == 900
    assert [b.failure(BlockedError("x"), 0) for _ in range(4)] == [21600, 43200, 86400, 86400]
    b.success()
    assert b.ready(0) and b.failures == 0


class FakeAdapter(OddsAdapter):
    def __init__(self, book, games=None, error=None):
        self.book, self.source, self.games, self.error, self.calls = book, f"{book}_fake", games or [], error, []

    def fetch(self, league):
        self.calls.append(league)
        if self.error:
            raise self.error
        return self.games


class NoScores:
    def fetch(self, *a, **k):
        return []


def make_worker(conn, adapters, now, **settings_kw):
    settings = Settings(db_path=":memory:", enabled_leagues=("nba", "nfl", "mlb"), **settings_kw)
    clock = [now.timestamp()]
    w = Worker(settings, conn, adapters, NoScores(), clock=lambda: clock[0])
    w.last_run.update({f"schedule:{lg}": clock[0] for lg in settings.enabled_leagues})
    return w, clock


def test_worker_polls_each_book_league_at_most_once_per_minute(conn, settings):
    load_all(conn, settings, books=())
    fd = FakeAdapter("fanduel", FanDuelAdapter(None, settings).parse("nba", load_fixture("fanduel_nba.json"), RECORDED_AT))
    w, clock = make_worker(conn, {"fanduel": fd}, RECORDED_AT, odds_poll_seconds=5)
    assert w.settings.effective_odds_poll_seconds == 60
    for _ in range(59):
        w.tick()
        clock[0] += 1
    assert fd.calls == ["nba", "nfl"]  # MLB skipped: no scheduled games in the lookahead window
    clock[0] += 1
    w.tick()
    assert fd.calls == ["nba", "nfl", "nba", "nfl"]
    assert conn.execute("SELECT COUNT(*) FROM prices WHERE book_id = 'fanduel'").fetchone()[0] == 36


def test_blocked_book_backs_off_for_hours_and_is_reported(conn, settings):
    load_all(conn, settings, books=())
    dk = FakeAdapter("draftkings", error=BlockedError("HTTP 403 from sportsbook-nash.draftkings.com: anonymous access refused"))
    w, clock = make_worker(conn, {"draftkings": dk}, RECORDED_AT)
    w.tick()
    assert dk.calls == ["nba", "nfl"]
    for _ in range(100):
        clock[0] += 60
        w.tick()
    assert dk.calls == ["nba", "nfl"]  # nothing for 100 minutes
    row = conn.execute("SELECT * FROM poll_status WHERE job = 'odds:draftkings:nba'").fetchone()
    assert row["consecutive_failures"] == 1 and "403" in row["last_error"]
    next_allowed = datetime.fromisoformat(row["next_allowed_at"].replace("Z", "+00:00"))
    assert next_allowed - RECORDED_AT >= timedelta(hours=6) - timedelta(seconds=1)


def test_score_poll_cadence(conn, settings):
    load_all(conn, settings, books=())
    w, _ = make_worker(conn, {}, RECORDED_AT)
    assert w.score_interval("nba", RECORDED_AT) == 30  # games in progress
    assert w.score_interval("mlb", RECORDED_AT) is None  # offseason
    quiet = datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)
    conn.execute("UPDATE games SET status = 'final' WHERE starts_at < ?", ("2026-11-26T03:00:00Z",))
    assert w.score_interval("nfl", quiet) is None
