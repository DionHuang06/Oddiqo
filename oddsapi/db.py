import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    season TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    league_id TEXT NOT NULL REFERENCES leagues(id),
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    scores_external_id TEXT,
    UNIQUE (league_id, scores_external_id)
);

CREATE TABLE IF NOT EXISTS team_aliases (
    league_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(id),
    PRIMARY KEY (league_id, alias)
);

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    league_id TEXT NOT NULL REFERENCES leagues(id),
    season TEXT,
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL,
    home_team_id TEXT NOT NULL REFERENCES teams(id),
    away_team_id TEXT NOT NULL REFERENCES teams(id),
    venue TEXT,
    scores_source TEXT NOT NULL,
    scores_external_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (scores_source, scores_external_id)
);
CREATE INDEX IF NOT EXISTS games_league_start ON games (league_id, starts_at);

CREATE TABLE IF NOT EXISTS game_odds_ids (
    game_id TEXT NOT NULL REFERENCES games(id),
    book_id TEXT NOT NULL REFERENCES books(id),
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    PRIMARY KEY (book_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS scores (
    game_id TEXT PRIMARY KEY REFERENCES games(id),
    home_score INTEGER,
    away_score INTEGER,
    period INTEGER,
    period_detail TEXT,
    clock TEXT,
    linescore TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    game_id TEXT NOT NULL REFERENCES games(id),
    book_id TEXT NOT NULL REFERENCES books(id),
    market TEXT NOT NULL CHECK (market IN ('moneyline', 'spread', 'total')),
    side TEXT NOT NULL,
    line REAL,
    price_american INTEGER NOT NULL,
    price_decimal TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS prices_unique ON prices (game_id, book_id, market, side, IFNULL(line, -99999));

CREATE TABLE IF NOT EXISTS price_snapshots (
    game_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    market TEXT NOT NULL,
    side TEXT NOT NULL,
    line REAL,
    price_american INTEGER NOT NULL,
    price_decimal TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS price_snapshots_game ON price_snapshots (game_id, book_id, market, observed_at);

CREATE TABLE IF NOT EXISTS poll_status (
    job TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_allowed_at TEXT,
    last_count INTEGER
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    developer_name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL,
    scopes TEXT NOT NULL,
    league_id TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
"""

LEAGUE_ROWS = [("nba", "NBA", "basketball"), ("nfl", "NFL", "american_football"), ("mlb", "MLB", "baseball")]
BOOK_ROWS = [("fanduel", "FanDuel"), ("draftkings", "DraftKings"), ("pinnacle", "Pinnacle")]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executemany("INSERT OR IGNORE INTO leagues (id, name, sport) VALUES (?, ?, ?)", LEAGUE_ROWS)
    conn.executemany("INSERT OR IGNORE INTO books (id, name) VALUES (?, ?)", BOOK_ROWS)
    conn.commit()
