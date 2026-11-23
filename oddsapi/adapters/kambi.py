from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import parse_american
from .base import ScrapingAdapter, parse_ts

BASE = "https://eu-offering-api.kambicdn.com/offering/v2018"
CRITERIA = {
    "Moneyline - Including Overtime": "moneyline",
    "Point Spread - Including Overtime": "spread",
    "Total Points - Including Overtime": "total",
}
TOTAL_SIDES = {"OT_OVER": "over", "OT_UNDER": "under"}


class KambiAdapter(ScrapingAdapter):
    """Unibet's sportsbook pages read Kambi's public offering CDN. The league list carries
    only moneylines, so main spread/total lines come from one bet-offer call per event."""

    book = "unibet"
    source = "kambi_unibet"
    league_ids = {"nba": "basketball/nba"}

    def _params(self) -> dict:
        return {"lang": "en_US", "market": self.settings.kambi_market}

    def fetch_raw(self, league: str) -> dict:
        off = self.settings.kambi_offering
        listing = self.client.get_json(f"{BASE}/{off}/listView/{self.league_ids[league]}/all/all/matches.json", params=self._params())
        offers = {}
        for item in listing.get("events") or []:
            ev = item.get("event") or {}
            if ev.get("state") != "NOT_STARTED":
                continue
            offers[str(ev["id"])] = self.client.get_json(
                f"{BASE}/{off}/betoffer/event/{ev['id']}.json",
                params={**self._params(), "includeParticipants": "true", "type": "2,1,6"},
            )
        return {"list": listing, "offers": offers}

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games = []
        for item in (raw.get("list") or {}).get("events") or []:
            ev = item.get("event") or {}
            offer = (raw.get("offers") or {}).get(str(ev.get("id")))
            if ev.get("state") != "NOT_STARTED" or not offer:
                continue
            detail = (offer.get("events") or [ev])[0]
            participants = {p["participantId"]: ("home" if p.get("home") else "away") for p in detail.get("participants") or []}
            game = BookGame(
                external_id=str(ev["id"]),
                league=league,
                starts_at=parse_ts(ev["start"]),
                home_name=_english(detail, "home") or ev["homeName"],
                away_name=_english(detail, "away") or ev["awayName"],
                captured_at=captured_at,
            )
            for bo in offer.get("betOffers") or []:
                market = CRITERIA.get((bo.get("criterion") or {}).get("englishLabel"))
                tags = bo.get("tags") or []
                if not market or "MAIN_LINE" not in tags and market != "moneyline":
                    continue
                if any(m.market == market for m in game.markets):
                    continue
                outcomes = []
                for o in bo.get("outcomes") or []:
                    side = TOTAL_SIDES.get(o.get("type")) if market == "total" else participants.get(o.get("participantId"))
                    if not side or o.get("status") != "OPEN" or o.get("oddsAmerican") is None:
                        continue
                    line = None if market == "moneyline" else o["line"] / 1000
                    outcomes.append(Outcome(side=side, price_american=parse_american(o["oddsAmerican"]), line=line))
                if len(outcomes) == 2:
                    game.markets.append(BookMarket(market=market, outcomes=outcomes))
            if game.markets:
                games.append(game)
        return games


def _english(detail: dict, role: str) -> str | None:
    for p in detail.get("participants") or []:
        if bool(p.get("home")) == (role == "home"):
            return p.get("englishName")
    return None
