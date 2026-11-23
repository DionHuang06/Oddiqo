"""Normalize source data and write it to the store. Request handlers never call out."""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta

from .db import from_iso, iso, utcnow
from .models import BookGame, ScoreGame, ScoreTeam
from .odds_math import american_to_decimal

# Book spellings that the suffix rule in resolve_team cannot reach.
EXTRA_ALIASES = {
    "nba": {"la clippers": "LAC", "los angeles clippers": "LAC", "la lakers": "LAL", "los angeles lakers": "LAL"},
    "nfl": {"la rams": "LAR", "la chargers": "LAC", "ny giants": "NYG", "ny jets": "NYJ"},
    "mlb": {"oakland athletics": "ATH", "sacramento athletics": "ATH", "ny yankees": "NYY", "ny mets": "NYM"},
}


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower().replace("&", " and "))).strip()


def team_public_id(league: str, abbreviation: str) -> str:
    return f"t_{league}_{abbreviation.lower()}"


def upsert_team(conn: sqlite3.Connection, league: str, t: ScoreTeam) -> str:
    row = conn.execute(
        "SELECT id FROM teams WHERE league_id = ? AND scores_external_id = ?", (league, t.external_id)
    ).fetchone()
    team_id = row["id"] if row else team_public_id(league, t.abbreviation)
    conn.execute(
        """INSERT INTO teams (id, league_id, name, short_name, scores_external_id) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET name = excluded.name, short_name = excluded.short_name""",
        (team_id, league, t.name, t.short_name, t.external_id),
    )
    aliases = {t.name, t.abbreviation, t.nickname, f"{t.location} {t.nickname}"}
    aliases |= {a for a, abbr in EXTRA_ALIASES.get(league, {}).items() if abbr == t.abbreviation}
    for alias in filter(None, (normalize(a) for a in aliases if a)):
        conn.execute(
            "INSERT OR IGNORE INTO team_aliases (league_id, alias, team_id) VALUES (?, ?, ?)", (league, alias, team_id)
        )
    return team_id


def resolve_team(conn: sqlite3.Connection, league: str, name: str) -> str | None:
    words = normalize(name).split()
    # Full name first, then shorter suffixes so "LA Clippers" still finds "clippers".
    for i in range(len(words)):
        row = conn.execute(
            "SELECT team_id FROM team_aliases WHERE league_id = ? AND alias = ?", (league, " ".join(words[i:]))
        ).fetchone()
        if row:
            return row["team_id"]
    return None


def upsert_score_games(conn: sqlite3.Connection, games: list[ScoreGame], source: str = "espn") -> int:
    now = iso(utcnow())
    for g in games:
        home_id = upsert_team(conn, g.league, g.home)
        away_id = upsert_team(conn, g.league, g.away)
        row = conn.execute(
            "SELECT id FROM games WHERE scores_source = ? AND scores_external_id = ?", (source, g.external_id)
        ).fetchone()
        game_id = row["id"] if row else str(uuid.uuid4())
        conn.execute(
            """INSERT INTO games (id, league_id, season, starts_at, status, home_team_id, away_team_id, venue,
                                  scores_source, scores_external_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET season = excluded.season, starts_at = excluded.starts_at,
                   status = excluded.status, home_team_id = excluded.home_team_id,
                   away_team_id = excluded.away_team_id, venue = excluded.venue, updated_at = excluded.updated_at""",
            (game_id, g.league, g.season, iso(g.starts_at), g.status, home_id, away_id, g.venue, source, g.external_id, now),
        )
        conn.execute("UPDATE leagues SET season = ? WHERE id = ?", (g.season, g.league))
        if g.home_score is not None and g.away_score is not None:
            conn.execute(
                """INSERT INTO scores (game_id, home_score, away_score, period, period_detail, clock, linescore, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (game_id) DO UPDATE SET home_score = excluded.home_score,
                       away_score = excluded.away_score, period = excluded.period,
                       period_detail = excluded.period_detail, clock = excluded.clock,
                       linescore = excluded.linescore, captured_at = excluded.captured_at""",
                (
                    game_id, g.home_score, g.away_score, g.period, g.period_detail, g.clock,
                    json.dumps(g.linescore) if g.linescore is not None else None, iso(g.captured_at),
                ),
            )
    conn.commit()
    return len(games)


def match_game(conn: sqlite3.Connection, book: str, source: str, bg: BookGame, tolerance_minutes: int) -> str | None:
    row = conn.execute(
        "SELECT game_id FROM game_odds_ids WHERE book_id = ? AND source = ? AND external_id = ?",
        (book, source, bg.external_id),
    ).fetchone()
    if row:
        return row["game_id"]
    home = resolve_team(conn, bg.league, bg.home_name)
    away = resolve_team(conn, bg.league, bg.away_name)
    if not home or not away:
        return None
    tol = timedelta(minutes=tolerance_minutes)
    candidates = conn.execute(
        """SELECT id, starts_at FROM games WHERE league_id = ? AND home_team_id = ? AND away_team_id = ?
           AND starts_at BETWEEN ? AND ?""",
        (bg.league, home, away, iso(bg.starts_at - tol), iso(bg.starts_at + tol)),
    ).fetchall()
    if not candidates:
        return None
    best = min(candidates, key=lambda r: abs((from_iso(r["starts_at"]) - bg.starts_at).total_seconds()))
    conn.execute(
        "INSERT OR IGNORE INTO game_odds_ids (game_id, book_id, source, external_id) VALUES (?, ?, ?, ?)",
        (best["id"], book, source, bg.external_id),
    )
    return best["id"]


def ingest_book_games(
    conn: sqlite3.Connection, book: str, source: str, games: list[BookGame], tolerance_minutes: int, now: datetime | None = None
) -> dict:
    observed = iso(now or utcnow())
    stats = {"games": len(games), "matched": 0, "unmatched": [], "prices": 0, "snapshots": 0}
    for bg in games:
        game_id = match_game(conn, book, source, bg, tolerance_minutes)
        if not game_id:
            stats["unmatched"].append(f"{bg.away_name} @ {bg.home_name} {iso(bg.starts_at)}")
            continue
        stats["matched"] += 1
        teams = conn.execute("SELECT home_team_id, away_team_id FROM games WHERE id = ?", (game_id,)).fetchone()
        side_ids = {"home": teams["home_team_id"], "away": teams["away_team_id"], "over": "over", "under": "under"}
        captured = iso(bg.captured_at) if bg.captured_at else observed

        for bm in bg.markets:
            existing = {
                r["side"]: (r["line"], r["price_american"])
                for r in conn.execute(
                    "SELECT side, line, price_american FROM prices WHERE game_id = ? AND book_id = ? AND market = ?",
                    (game_id, book, bm.market),
                )
            }
            conn.execute("DELETE FROM prices WHERE game_id = ? AND book_id = ? AND market = ?", (game_id, book, bm.market))
            for o in bm.outcomes:
                side = side_ids[o.side]
                row = (game_id, book, bm.market, side, o.line, o.price_american, american_to_decimal(o.price_american), captured, source)
                conn.execute(
                    """INSERT INTO prices (game_id, book_id, market, side, line, price_american, price_decimal, captured_at, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    row,
                )
                stats["prices"] += 1
                if existing.get(side) != (o.line, o.price_american):
                    conn.execute(
                        """INSERT INTO price_snapshots (game_id, book_id, market, side, line, price_american, price_decimal,
                                                        captured_at, source, observed_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        row + (observed,),
                    )
                    stats["snapshots"] += 1
    conn.commit()
    return stats


def record_poll(conn: sqlite3.Connection, job: str, ok: bool, error: str | None = None, count: int | None = None,
                next_allowed: datetime | None = None) -> None:
    now = iso(utcnow())
    conn.execute(
        """INSERT INTO poll_status (job, last_attempt_at, last_success_at, last_error, consecutive_failures, next_allowed_at, last_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (job) DO UPDATE SET last_attempt_at = excluded.last_attempt_at,
               last_success_at = COALESCE(excluded.last_success_at, poll_status.last_success_at),
               last_error = excluded.last_error,
               consecutive_failures = CASE WHEN ? THEN 0 ELSE poll_status.consecutive_failures + 1 END,
               next_allowed_at = excluded.next_allowed_at,
               last_count = COALESCE(excluded.last_count, poll_status.last_count)""",
        (job, now, now if ok else None, error, 0 if ok else 1, iso(next_allowed) if next_allowed else None, count, ok),
    )
    conn.commit()
