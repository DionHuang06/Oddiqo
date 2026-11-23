from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import parse_american
from .base import ScrapingAdapter, parse_ts

SIDES = {"HOME": "home", "AWAY": "away", "OVER": "over", "UNDER": "under"}


def _market(bet_offer: dict) -> str | None:
    desc = (bet_offer.get("betDescription") or "").lower()
    kind = bet_offer.get("betOfferType")
    if kind == "TWO_WAY" and desc == "moneyline":
        return "moneyline"
    if kind == "TWO_WAY_HANDICAP" and desc == "point spread":
        return "spread"
    if kind == "OVER_UNDER" and desc.startswith("total points"):
        return "total"
    return None


class BetRiversAdapter(ScrapingAdapter):
    """Reads the list-view JSON that betrivers.com's own league page calls (Rush Street Interactive)."""

    book = "betrivers"
    source = "betrivers_web"
    league_ids = {"nba": "1000093652"}

    def fetch_raw(self, league: str) -> dict:
        items = []
        for page in range(1, 4):  # the API caps pageSize at 20
            data = self.client.get_json(
                f"https://{self.settings.betrivers_host}/api/service/sportsbook/offering/listview/events",
                params={
                    "cageCode": self.settings.betrivers_cage,
                    "type": "prematch",
                    "groupId": self.league_ids[league],
                    "pageNr": page,
                    "pageSize": 20,
                    "offset": 0,
                },
            )
            items += data.get("items") or []
            if page >= ((data.get("paging") or {}).get("totalPages") or 1):
                break
        return {"items": items}

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games = []
        for ev in raw.get("items") or []:
            if ev.get("state") != "NOT_STARTED":
                continue
            names = {("home" if p.get("home") else "away"): p.get("name") for p in ev.get("participants") or []}
            if not (names.get("home") and names.get("away")):
                continue
            game = BookGame(
                external_id=str(ev["id"]),
                league=league,
                starts_at=parse_ts(ev["start"]),
                home_name=names["home"],
                away_name=names["away"],
                captured_at=captured_at,
            )
            for bo in ev.get("betOffers") or []:
                market = _market(bo)
                if not market or bo.get("status") != "OPEN" or any(m.market == market for m in game.markets):
                    continue
                outcomes = []
                for o in bo.get("outcomes") or []:
                    side = SIDES.get(o.get("type"))
                    if not side or o.get("status") != "OPEN" or o.get("oddsAmerican") is None:
                        continue
                    line = None if market == "moneyline" else float(o["line"])
                    outcomes.append(Outcome(side=side, price_american=parse_american(o["oddsAmerican"]), line=line))
                if len(outcomes) == 2:
                    game.markets.append(BookMarket(market=market, outcomes=outcomes))
            if game.markets:
                games.append(game)
        return games
