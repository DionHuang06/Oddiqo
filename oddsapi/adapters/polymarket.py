import json
import re
from datetime import datetime, timezone

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import prob_to_american
from .base import MAX_EXCHANGE_OVERROUND, ScrapingAdapter, parse_ts

API = "https://gamma-api.polymarket.com/events"
GAME_SLUG = re.compile(r"^nba-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}$")
TYPES = {"moneyline": "moneyline", "spreads": "spread", "totals": "total"}


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(v) -> list:
    return json.loads(v) if isinstance(v, str) else list(v or [])


class PolymarketAdapter(ScrapingAdapter):
    """Polymarket's public Gamma API (the JSON its own site reads). Each two-outcome market
    is priced at the best ask for a $1 share: outcome 0 at bestAsk, outcome 1 at
    1 - bestBid. Of the many spread/total lines, the one trading closest to 50/50 is kept."""

    book = "polymarket"
    source = "polymarket_gamma"
    league_ids = {"nba": "10345"}

    def fetch_raw(self, league: str) -> dict:
        events = self.client.get_json(
            API,
            params={"series_id": self.league_ids[league], "closed": "false", "limit": 60, "order": "startTime", "ascending": "true",
                    "end_date_min": datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")},
        )
        return {"events": events}

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games = []
        for ev in raw.get("events") or []:
            if not GAME_SLUG.match(ev.get("slug", "")) or ev.get("closed") or not ev.get("startTime"):
                continue
            starts = parse_ts(ev["startTime"])
            if starts <= captured_at:
                continue
            away, sep, home = (ev.get("title") or "").partition(" vs. ")
            if not sep:
                continue
            game = BookGame(external_id=ev["slug"], league=league, starts_at=starts, home_name=home, away_name=away, captured_at=captured_at)
            candidates: dict[str, list] = {}
            for m in ev.get("markets") or []:
                market = TYPES.get(m.get("sportsMarketType"))
                bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
                if not market or m.get("closed") or not m.get("active") or not m.get("acceptingOrders") or not bid or not ask:
                    continue
                if ask + (1 - bid) > MAX_EXCHANGE_OVERROUND:
                    continue
                candidates.setdefault(market, []).append((abs((bid + ask) / 2 - 0.5), m, bid, ask))
            for market in ("moneyline", "spread", "total"):
                if not candidates.get(market):
                    continue
                _, m, bid, ask = min(candidates[market], key=lambda c: c[0])
                outcomes = self._outcomes(market, m, bid, ask, home, away)
                if outcomes:
                    game.markets.append(BookMarket(market=market, outcomes=outcomes))
            if game.markets:
                games.append(game)
        return games

    def _outcomes(self, market, m, bid, ask, home, away) -> list[Outcome] | None:
        names = _list(m.get("outcomes"))
        p0, p1 = prob_to_american(ask), prob_to_american(1 - bid)
        if len(names) != 2 or p0 is None or p1 is None:
            return None
        if market == "total":
            line = float(m["line"])
            sides = {"Over": "over", "Under": "under"}
            if set(names) != set(sides):
                return None
            return [Outcome(sides[names[0]], p0, line), Outcome(sides[names[1]], p1, line)]
        role = {away: "away", home: "home"}
        if set(names) != set(role):
            return None
        if market == "moneyline":
            return [Outcome(role[names[0]], p0), Outcome(role[names[1]], p1)]
        line = float(m["line"])
        return [Outcome(role[names[0]], p0, line), Outcome(role[names[1]], p1, -line)]
