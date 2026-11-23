from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from .base import ScrapingAdapter, parse_ts

BASE = "https://guest.api.arcadia.pinnacle.com/0.1"


class PinnacleAdapter(ScrapingAdapter):
    """Reads the guest (logged-out) API that www.pinnacle.com's own pages call.
    Two requests per poll: matchups and straight markets for the league."""

    book = "pinnacle"
    source = "pinnacle_web"
    league_ids = {"nba": 487, "nfl": 889, "mlb": 246}

    def fetch_raw(self, league: str) -> dict:
        lid = self.league_ids[league]
        matchups = self.client.get_json(f"{BASE}/leagues/{lid}/matchups", params={"brandId": 0})
        markets = self.client.get_json(f"{BASE}/leagues/{lid}/markets/straight")
        return {"matchups": matchups, "markets": markets}

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games: dict[int, BookGame] = {}
        for m in raw.get("matchups") or []:
            if (
                m.get("type") != "matchup"
                or m.get("parent")
                or m.get("isLive")
                or m.get("units", "Regular") != "Regular"
                or m.get("status") != "pending"
            ):
                continue
            teams = {p.get("alignment"): p.get("name") for p in m.get("participants") or []}
            if not (teams.get("home") and teams.get("away")):
                continue
            games[m["id"]] = BookGame(
                external_id=str(m["id"]),
                league=league,
                starts_at=parse_ts(m["startTime"]),
                home_name=teams["home"],
                away_name=teams["away"],
                captured_at=captured_at,
            )

        for mk in raw.get("markets") or []:
            game = games.get(mk.get("matchupId"))
            market = mk.get("type")
            if (
                game is None
                or market not in ("moneyline", "spread", "total")
                or mk.get("period") != 0
                or mk.get("isAlternate", True)
                or mk.get("status") not in (None, "open")
                or any(bm.market == market for bm in game.markets)
            ):
                continue
            outcomes = []
            for p in mk.get("prices") or []:
                side = p.get("designation")
                if side not in ("home", "away", "over", "under") or p.get("price") is None:
                    continue
                line = None if market == "moneyline" else p.get("points")
                if market != "moneyline" and line is None:
                    continue
                outcomes.append(Outcome(side=side, price_american=int(p["price"]), line=None if line is None else float(line)))
            if len(outcomes) == 2:
                game.markets.append(BookMarket(market=market, outcomes=outcomes))
        return [g for g in games.values() if g.markets]
