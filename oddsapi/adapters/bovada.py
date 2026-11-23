from datetime import datetime, timezone

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import parse_american
from .base import ScrapingAdapter

MARKET_KEYS = {"2W-12": "moneyline", "2W-HCAP": "spread", "2W-OU": "total"}
SIDES = {"H": "home", "A": "away", "O": "over", "U": "under"}


class BovadaAdapter(ScrapingAdapter):
    """Reads the event-coupon JSON that bovada.lv's own league page calls."""

    book = "bovada"
    source = "bovada_web"
    league_ids = {"nba": "basketball/nba"}

    def fetch_raw(self, league: str) -> dict:
        data = self.client.get_json(
            f"https://www.bovada.lv/services/sports/event/v2/events/A/description/{self.league_ids[league]}",
            params={"marketFilterId": "def", "preMatchOnly": "true", "eventsLimit": 50, "lang": "en"},
        )
        return {"groups": data}

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games = []
        for group in raw.get("groups") or []:
            for ev in group.get("events") or []:
                if ev.get("live"):
                    continue
                names = {("home" if c.get("home") else "away"): c.get("name") for c in ev.get("competitors") or []}
                if not (names.get("home") and names.get("away")):
                    continue
                game = BookGame(
                    external_id=str(ev["id"]),
                    league=league,
                    starts_at=datetime.fromtimestamp(ev["startTime"] / 1000, timezone.utc),
                    home_name=names["home"],
                    away_name=names["away"],
                    captured_at=captured_at,
                )
                for dg in ev.get("displayGroups") or []:
                    for m in dg.get("markets") or []:
                        market = MARKET_KEYS.get(m.get("key"))
                        period = m.get("period") or {}
                        if (
                            not market
                            or not period.get("main")
                            or m.get("status") != "O"
                            or any(x.market == market for x in game.markets)
                        ):
                            continue
                        outcomes = []
                        for o in m.get("outcomes") or []:
                            side = SIDES.get(o.get("type"))
                            price = o.get("price") or {}
                            if not side or o.get("status") != "O" or price.get("american") is None:
                                continue
                            line = None if market == "moneyline" else float(price["handicap"])
                            outcomes.append(Outcome(side=side, price_american=parse_american(price["american"]), line=line))
                        if len(outcomes) == 2:
                            game.markets.append(BookMarket(market=market, outcomes=outcomes))
                if game.markets:
                    games.append(game)
        return games
