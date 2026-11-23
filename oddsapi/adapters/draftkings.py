"""DraftKings adapter over the `sportscontent` league endpoint that
sportsbook.draftkings.com's own league pages call (events, markets, selections).

DraftKings' CDN answers some HTTP clients with 403 "Access Denied" (curl was refused
from the development VM; this project's httpx client with the same User-Agent was
served). A 403 surfaces as BlockedError and the worker backs off for hours. Do not add
browser headers, TLS impersonation or proxies to get past it.
"""

from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import parse_american
from .base import ScrapingAdapter, parse_ts

MARKET_NAMES = {
    "moneyline": "moneyline",
    "spread": "spread",
    "run line": "spread",
    "point spread": "spread",
    "total": "total",
    "total points": "total",
    "total runs": "total",
}


class DraftKingsAdapter(ScrapingAdapter):
    book = "draftkings"
    source = "draftkings_web"
    league_ids = {"nba": 42648, "nfl": 88808, "mlb": 84240}

    def fetch_raw(self, league: str) -> dict:
        site = self.settings.draftkings_site
        return self.client.get_json(
            f"https://sportsbook-nash.draftkings.com/api/sportscontent/{site}/v1/leagues/{self.league_ids[league]}"
        )

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games: dict[str, BookGame] = {}
        for e in raw.get("events") or []:
            if e.get("status") not in (None, "NOT_STARTED"):
                continue
            roles = {(p.get("venueRole") or "").lower(): p.get("name") for p in e.get("participants") or []}
            home = roles.get("home") or roles.get("homeplayer")
            away = roles.get("away") or roles.get("awayplayer")
            if not (home and away and e.get("startEventDate")):
                continue
            games[str(e["id"])] = BookGame(
                external_id=str(e["id"]),
                league=league,
                starts_at=parse_ts(e["startEventDate"]),
                home_name=home,
                away_name=away,
                captured_at=captured_at,
            )

        market_info: dict[str, tuple[str, str]] = {}
        for m in raw.get("markets") or []:
            name = ((m.get("marketType") or {}).get("name") or m.get("name") or "").strip().lower()
            market = MARKET_NAMES.get(name)
            if market and str(m.get("eventId")) in games and not m.get("isSuspended"):
                market_info[str(m["id"])] = (str(m["eventId"]), market)

        grouped: dict[str, list[Outcome]] = {}
        for s in raw.get("selections") or []:
            info = market_info.get(str(s.get("marketId")))
            if not info:
                continue
            side = (s.get("outcomeType") or "").lower()
            if side not in ("home", "away", "over", "under"):
                continue
            american = (s.get("displayOdds") or {}).get("american")
            if american is None:
                continue
            market = info[1]
            line = None if market == "moneyline" else s.get("points")
            if market != "moneyline" and line is None:
                continue
            grouped.setdefault(str(s["marketId"]), []).append(
                Outcome(side=side, price_american=parse_american(american), line=None if line is None else float(line))
            )

        for market_id, outcomes in grouped.items():
            event_id, market = market_info[market_id]
            game = games[event_id]
            if len(outcomes) == 2 and not any(bm.market == market for bm in game.markets):
                game.markets.append(BookMarket(market=market, outcomes=outcomes))
        return [g for g in games.values() if g.markets]
