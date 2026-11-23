import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oddsapi.adapters import DraftKingsAdapter, FanDuelAdapter, PinnacleAdapter
from oddsapi.config import Settings
from oddsapi.db import connect, init_db
from oddsapi.ingest import ingest_book_games, upsert_score_games
from oddsapi.scores.espn import parse_scoreboard

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED_AT = datetime(2026, 11, 23, 1, 17, 30, tzinfo=timezone.utc)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())["data"]


@pytest.fixture
def settings():
    return Settings(db_path=":memory:")


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


def load_all(conn, settings, books=("fanduel", "pinnacle", "draftkings")):
    for lg in ("nba", "nfl", "mlb"):
        upsert_score_games(conn, parse_scoreboard(lg, load_fixture(f"espn_{lg}.json"), RECORDED_AT))
        if (FIXTURES / f"espn_{lg}_schedule.json").exists():
            upsert_score_games(conn, parse_scoreboard(lg, load_fixture(f"espn_{lg}_schedule.json"), RECORDED_AT))
    adapters = {"fanduel": FanDuelAdapter, "pinnacle": PinnacleAdapter, "draftkings": DraftKingsAdapter}
    for book in books:
        a = adapters[book](None, settings)
        for lg in ("nba", "nfl", "mlb"):
            games = a.parse(lg, load_fixture(f"{book}_{lg}.json"), RECORDED_AT)
            ingest_book_games(conn, book, a.source, games, 15, RECORDED_AT)


@pytest.fixture
def loaded(conn, settings):
    load_all(conn, settings)
    return conn
