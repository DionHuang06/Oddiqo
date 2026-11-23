"""Read API. Serves only what the worker stored; never calls a sportsbook or ESPN."""

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import ApiKey, RateLimiter, lookup_key
from .config import BOOKS, LEAGUES, MARKETS, Settings, get_settings
from .db import connect, from_iso, init_db, utcnow

GAME_STATUSES = ("scheduled", "live", "final", "postponed", "canceled")
SPORTS = {"nba": "basketball", "nfl": "american_football", "mlb": "baseball"}


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def create_app(settings: Settings | None = None, conn: sqlite3.Connection | None = None, limiter: RateLimiter | None = None, clock=utcnow) -> FastAPI:
    settings = settings or get_settings()
    if conn is None:
        conn = connect(settings.db_path)
        init_db(conn)
    limiter = limiter or RateLimiter()
    app = FastAPI(title="Scores and odds API (prototype)", version="0.1.0")

    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError):
        return _error(exc.status, exc.code, exc.message, getattr(request.state, "rl_headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return _error(400, "bad_request", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, "not_found" if exc.status_code == 404 else "error", str(exc.detail))

    @app.middleware("http")
    async def _rate_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in (getattr(request.state, "rl_headers", None) or {}).items():
            response.headers.setdefault(k, v)
        return response

    def require(scope: str | None):
        def dep(request: Request) -> ApiKey:
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                raise APIError(401, "unauthorized", "Missing bearer API key")
            key = lookup_key(conn, auth[7:].strip())
            if key is None:
                raise APIError(401, "unauthorized", "Unknown or revoked API key")
            allowed, headers = limiter.hit(key.id, key.tier)
            request.state.rl_headers = headers
            if not allowed:
                raise APIError(429, "rate_limited", "Rate limit exceeded")
            if scope and scope not in key.scopes:
                raise APIError(403, "forbidden", f"Key lacks scope {scope}")
            return key

        return dep

    def check_league(key: ApiKey, league: str) -> None:
        if key.league_id and key.league_id != league:
            raise APIError(403, "forbidden", f"Key is limited to league {key.league_id}")

    def parse_list(raw: str | None, allowed: tuple[str, ...], name: str) -> list[str]:
        if raw is None:
            return list(allowed)
        items = [x.strip() for x in raw.split(",") if x.strip()]
        if not items or (bad := [x for x in items if x not in allowed]):
            raise APIError(400, "bad_request", f"{name} must be a comma list of {', '.join(allowed)}")
        return items

    def day_window(league: str | None, on: str | None, time_zone: str) -> tuple[str, str]:
        if league is None or on is None:
            raise APIError(400, "bad_request", "league and on are required")
        if league not in LEAGUES:
            raise APIError(400, "bad_request", f"league must be one of {', '.join(LEAGUES)}")
        try:
            day = date.fromisoformat(on)
        except ValueError:
            raise APIError(400, "bad_request", "on must be a date, YYYY-MM-DD") from None
        try:
            tz = ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise APIError(400, "bad_request", "time_zone must be an IANA zone name") from None
        start = datetime.combine(day, time.min, tz).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, tz).astimezone(timezone.utc)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")

    GAME_SQL = """
        SELECT g.*, h.name AS home_name, h.short_name AS home_short, a.name AS away_name, a.short_name AS away_short,
               s.home_score, s.away_score, s.period, s.period_detail, s.clock, s.linescore, s.captured_at AS score_captured_at
        FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
        LEFT JOIN scores s ON s.game_id = g.id
    """

    def score_obj(r: sqlite3.Row, detail: bool) -> dict | None:
        if r["status"] not in ("live", "final") or r["score_captured_at"] is None:
            return None
        age = (clock() - from_iso(r["score_captured_at"])).total_seconds()
        obj = {
            "home": r["home_score"],
            "away": r["away_score"],
            "period": r["period"],
            "period_detail": "final" if r["status"] == "final" else r["period_detail"],
            "clock": None if r["status"] == "final" else r["clock"],
        }
        if detail and r["league_id"] == "mlb" and r["linescore"]:
            obj["linescore"] = json.loads(r["linescore"])
        elif detail and r["league_id"] == "mlb":
            obj["linescore"] = None
        obj["captured_at"] = r["score_captured_at"]
        obj["stale"] = r["status"] == "live" and age > settings.live_score_stale_seconds
        return obj

    def game_stub(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "league": r["league_id"],
            "starts_at": r["starts_at"],
            "status": r["status"],
            "home": {"id": r["home_team_id"], "name": r["home_name"], "short_name": r["home_short"]},
            "away": {"id": r["away_team_id"], "name": r["away_name"], "short_name": r["away_short"]},
        }

    def books_for(games: list[sqlite3.Row], books: list[str], markets: list[str]) -> dict[str, list]:
        out = {g["id"]: [] for g in games}
        if not games:
            return out
        teams = {g["id"]: (g["away_team_id"], g["home_team_id"]) for g in games}
        ids = list(out)
        rows = conn.execute(
            f"""SELECT * FROM prices WHERE game_id IN ({','.join('?' * len(ids))})
                AND book_id IN ({','.join('?' * len(books))}) AND market IN ({','.join('?' * len(markets))})""",
            ids + books + markets,
        ).fetchall()
        now = clock()
        grouped: dict[tuple, list] = {}
        for r in rows:
            age = (now - from_iso(r["captured_at"])).total_seconds()
            if age > settings.odds_max_age_seconds:
                continue
            grouped.setdefault((r["game_id"], r["book_id"], r["market"]), []).append((r, age))
        for gid in ids:
            away_id, home_id = teams[gid]
            order = {away_id: 0, home_id: 1, "over": 0, "under": 1}
            for book in BOOKS:
                if book not in books:
                    continue
                mlist = []
                for market in MARKETS:
                    items = grouped.get((gid, book, market))
                    if market not in markets or not items:
                        continue
                    items.sort(key=lambda x: order.get(x[0]["side"], 9))
                    outcomes = []
                    for r, _ in items:
                        o = {"side": r["side"]}
                        if market != "moneyline":
                            o["line"] = r["line"]
                        o["price_american"] = r["price_american"]
                        o["price_decimal"] = r["price_decimal"]
                        outcomes.append(o)
                    mlist.append({
                        "id": market,
                        "outcomes": outcomes,
                        "captured_at": min(r["captured_at"] for r, _ in items),
                        "stale": max(a for _, a in items) > settings.odds_stale_seconds,
                    })
                if mlist:
                    out[gid].append({"id": book, "markets": mlist})
        return out

    def games_on(league, on, time_zone, status=None) -> list[sqlite3.Row]:
        start, end = day_window(league, on, time_zone)
        sql, params = GAME_SQL + " WHERE g.league_id = ? AND g.starts_at >= ? AND g.starts_at < ?", [league, start, end]
        if status is not None:
            statuses = parse_list(status, GAME_STATUSES, "status")
            sql += f" AND g.status IN ({','.join('?' * len(statuses))})"
            params += statuses
        return conn.execute(sql + " ORDER BY g.starts_at, g.id", params).fetchall()

    def game_or_404(game_id: str, key: ApiKey) -> sqlite3.Row:
        r = conn.execute(GAME_SQL + " WHERE g.id = ?", (game_id,)).fetchone()
        if r is None:
            raise APIError(404, "not_found", f"No game {game_id}")
        check_league(key, r["league_id"])
        return r

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @app.get("/v1/leagues")
    def leagues(key: ApiKey = Depends(require(None))):
        rows = conn.execute("SELECT id, name, sport FROM leagues").fetchall()
        by_id = {r["id"]: r for r in rows}
        return {"data": [{"id": i, "name": by_id[i]["name"], "sport": by_id[i]["sport"]} for i in LEAGUES if i in by_id]}

    @app.get("/v1/books")
    def books(key: ApiKey = Depends(require(None))):
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM books")}
        return {"data": [{"id": b, "name": names[b]} for b in BOOKS if b in names]}

    @app.get("/v1/games")
    def list_games(league: str | None = None, on: str | None = None, time_zone: str = "America/New_York",
                   status: str | None = None, key: ApiKey = Depends(require("scores:read"))):
        if league in LEAGUES:
            check_league(key, league)
        rows = games_on(league, on, time_zone, status)
        data = [{**game_stub(r), "score": score_obj(r, detail=False)} for r in rows]
        return {"data": data, "meta": {"count": len(data), "league": league, "on": on, "time_zone": time_zone}}

    @app.get("/v1/games/{game_id}")
    def get_game(game_id: str, key: ApiKey = Depends(require("scores:read"))):
        r = game_or_404(game_id, key)
        stub = game_stub(r)
        data = {
            "id": stub["id"], "league": stub["league"], "season": r["season"], "starts_at": stub["starts_at"],
            "status": stub["status"], "venue": r["venue"], "home": stub["home"], "away": stub["away"],
            "score": score_obj(r, detail=True),
        }
        return {"data": data}

    @app.get("/v1/games/{game_id}/odds")
    def game_odds(game_id: str, books: str | None = None, markets: str | None = None, key: ApiKey = Depends(require("odds:read"))):
        r = game_or_404(game_id, key)
        b, m = parse_list(books, BOOKS, "books"), parse_list(markets, MARKETS, "markets")
        return {"data": {"game_id": r["id"], "books": books_for([r], b, m)[r["id"]]}}

    @app.get("/v1/odds")
    def board(league: str | None = None, on: str | None = None, time_zone: str = "America/New_York",
              books: str | None = None, markets: str | None = None, key: ApiKey = Depends(require("odds:read"))):
        if league in LEAGUES:
            check_league(key, league)
        b, m = parse_list(books, BOOKS, "books"), parse_list(markets, MARKETS, "markets")
        rows = games_on(league, on, time_zone)
        by_game = books_for(rows, b, m)
        data = [{**game_stub(r), "books": by_game[r["id"]]} for r in rows]
        return {"data": data, "meta": {"count": len(data), "league": league, "on": on, "time_zone": time_zone}}

    return app


def app_factory() -> FastAPI:
    return create_app()
