"""Private ingestion worker. Usage:

    python -m oddsapi.worker            # run forever
    python -m oddsapi.worker --once     # schedule + scores + one odds poll per book/league, then exit

Leagues in ODDSAPI_SLATE_LEAGUES (default nba) run on the day-slate schedule: today's
ET games are reloaded every ODDSAPI_SLATE_REFRESH_SECONDS (1800) and each book's odds
are polled every ODDSAPI_NBA_ODDS_POLL_SECONDS (1800) while any of them is pregame.
"""

import argparse
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .adapters import OddsAdapter, build_adapters
from .config import Settings, get_settings
from .db import connect, init_db, iso
from .http import Backoff, FetchError, PoliteClient
from .ingest import ingest_book_games, record_poll, upsert_score_games
from .scores import EspnScores

log = logging.getLogger("oddsapi.worker")
ET = ZoneInfo("America/New_York")


class Worker:
    def __init__(self, settings: Settings, conn: sqlite3.Connection, adapters: dict[str, OddsAdapter], scores, clock=time.time):
        self.settings = settings
        self.conn = conn
        self.adapters = adapters
        self.scores = scores
        self.clock = clock
        self.backoffs: dict[str, Backoff] = {}
        self.last_run: dict[str, float] = {}

    def _backoff(self, job: str) -> Backoff:
        s = self.settings
        if job not in self.backoffs:
            self.backoffs[job] = Backoff(s.backoff_base_seconds, s.backoff_max_seconds, s.blocked_backoff_seconds, s.blocked_backoff_max_seconds)
        return self.backoffs[job]

    def _due(self, job: str, interval: float, now: float) -> bool:
        return self._backoff(job).ready(now) and now - self.last_run.get(job, float("-inf")) >= interval

    def _run(self, job: str, fn) -> object | None:
        now = self.clock()
        self.last_run[job] = now
        bo = self._backoff(job)
        try:
            result = fn()
        except FetchError as e:
            delay = bo.failure(e, now)
            log.warning("%s failed (%s); backing off %.0fs", job, bo.last_error, delay)
            record_poll(self.conn, job, False, bo.last_error, next_allowed=datetime.fromtimestamp(bo.not_before, timezone.utc))
            return None
        bo.success()
        count = result.get("matched") if isinstance(result, dict) else result
        record_poll(self.conn, job, True, count=count)
        return result

    def run_schedule(self, league: str):
        today = datetime.now(ET).date()
        return self._run(
            f"schedule:{league}",
            lambda: upsert_score_games(self.conn, self.scores.fetch(league, today - timedelta(days=1), today + timedelta(days=self.settings.schedule_days_ahead))),
        )

    def score_interval(self, league: str, now: datetime) -> float | None:
        """Live games: fast poll. Games starting within two hours: slow poll. Otherwise none."""
        q = "SELECT COUNT(*) FROM games WHERE league_id = ? AND "
        live = self.conn.execute(
            q + "(status = 'live' OR (status = 'scheduled' AND starts_at <= ? AND starts_at >= ?))",
            (league, iso(now), iso(now - timedelta(hours=6))),
        ).fetchone()[0]
        if live:
            return self.settings.live_score_poll_seconds
        soon = self.conn.execute(
            q + "status = 'scheduled' AND starts_at BETWEEN ? AND ?", (league, iso(now), iso(now + timedelta(hours=2)))
        ).fetchone()[0]
        return self.settings.soon_score_poll_seconds if soon else None

    def run_scores(self, league: str):
        today = datetime.now(ET).date()
        return self._run(f"scores:{league}", lambda: upsert_score_games(self.conn, self.scores.fetch(league, today - timedelta(days=1), today)))

    def league_has_upcoming(self, league: str, now: datetime) -> bool:
        return bool(
            self.conn.execute(
                "SELECT 1 FROM games WHERE league_id = ? AND status = 'scheduled' AND starts_at BETWEEN ? AND ? LIMIT 1",
                (league, iso(now), iso(now + timedelta(hours=self.settings.odds_lookahead_hours))),
            ).fetchone()
        )

    def slate_window(self, now: datetime) -> tuple[datetime, datetime]:
        """Today in ET (plus slate_days - 1 more days), as a UTC window."""
        today = now.astimezone(ET).date()
        start = datetime.combine(today, datetime.min.time(), ET)
        return start, start + timedelta(days=max(1, self.settings.slate_days))

    def run_slate(self, league: str, now: datetime):
        start, end = self.slate_window(now)
        first, last = start.date(), (end - timedelta(days=1)).date()
        return self._run(f"slate:{league}", lambda: upsert_score_games(self.conn, self.scores.fetch(league, first, last)))

    def slate_pregame(self, league: str, now: datetime) -> list[str]:
        """Ids of slate games that have not started; odds are polled only while this is non-empty."""
        start, end = self.slate_window(now)
        rows = self.conn.execute(
            """SELECT id FROM games WHERE league_id = ? AND status = 'scheduled'
               AND starts_at >= ? AND starts_at < ? AND starts_at > ? ORDER BY starts_at""",
            (league, iso(start), iso(end), iso(now)),
        ).fetchall()
        return [r["id"] for r in rows]

    def run_odds(self, book: str, league: str):
        adapter = self.adapters[book]

        def job():
            games = adapter.fetch(league)
            stats = ingest_book_games(self.conn, book, adapter.source, games, self.settings.match_tolerance_minutes)
            log.info("%s %s: %d games, %d matched, %d prices", book, league, stats["games"], stats["matched"], stats["prices"])
            if stats["unmatched"]:
                log.info("%s %s unmatched: %s", book, league, "; ".join(stats["unmatched"][:5]))
            return stats

        return self._run(f"odds:{book}:{league}", job)

    def tick(self) -> None:
        now_ts = self.clock()
        now = datetime.fromtimestamp(now_ts, timezone.utc)
        for league in self.settings.enabled_leagues:
            if self._due(f"schedule:{league}", self.settings.schedule_poll_seconds, now_ts):
                self.run_schedule(league)
            interval = self.score_interval(league, now)
            if interval and self._due(f"scores:{league}", interval, now_ts):
                self.run_scores(league)
            if league in self.settings.slate_leagues:
                if self._due(f"slate:{league}", self.settings.slate_refresh_seconds, now_ts):
                    self.run_slate(league, now)
                if not self.slate_pregame(league, now):
                    continue
            elif not self.league_has_upcoming(league, now):
                continue
            for book, adapter in self.adapters.items():
                if getattr(adapter, "supports", lambda _l: True)(league) and self._due(
                    f"odds:{book}:{league}", self.settings.odds_poll_for(league), now_ts
                ):
                    self.run_odds(book, league)

    def run_forever(self, idle_seconds: float = 1.0) -> None:
        while True:
            self.tick()
            time.sleep(idle_seconds)


def build_worker(settings: Settings | None = None) -> Worker:
    settings = settings or get_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    client = PoliteClient(settings)
    return Worker(settings, conn, build_adapters(client, settings), EspnScores(client))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = build_worker()
    if worker.settings.odds_poll_seconds < worker.settings.effective_odds_poll_seconds:
        log.warning("ODDSAPI_ODDS_POLL_SECONDS raised to %ss floor", worker.settings.effective_odds_poll_seconds)
    if args.once:
        worker.tick()
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
