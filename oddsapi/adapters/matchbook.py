from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import decimal_to_american
from .base import MAX_EXCHANGE_OVERROUND, ScrapingAdapter, parse_ts

TYPES = {"money_line": "moneyline", "handicap": "spread", "total": "total"}


def _back(runner: dict) -> float | None:
    for p in runner.get("prices") or []:
        if p.get("side") == "win" and p.get("decimal-odds"):
            return float(p["decimal-odds"])
    return None


class MatchbookAdapter(ScrapingAdapter):
    """Matchbook betting exchange's public REST API (logged-out event and price reads).
    Prices are the best back odds before Matchbook's commission. Of the several
    handicap/total lines it lists, the one closest to an even book is kept."""

    book = "matchbook"
    source = "matchbook_api"
    league_ids = {"nba": "NBA"}

    def fetch_raw(self, league: str) -> dict:
        return self.client.get_json(
            "https://api.matchbook.com/edge/rest/events",
            params={"sport-ids": 4, "include-prices": "true", "price-depth": 1, "per-page": 100, "states": "open"},
        )

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games = []
        for ev in raw.get("events") or []:
            tags = {(t.get("type"), t.get("name")) for t in ev.get("meta-tags") or []}
            if ("COMPETITION", self.league_ids[league]) not in tags or ev.get("in-running-flag"):
                continue
            away, sep, home = (ev.get("name") or "").partition(" at ")
            if not sep:
                continue
            starts = parse_ts(ev["start"])
            if starts <= captured_at:
                continue
            game = BookGame(external_id=str(ev["id"]), league=league, starts_at=starts, home_name=home, away_name=away, captured_at=captured_at)
            best: dict[str, tuple[float, list[Outcome]]] = {}
            for m in ev.get("markets") or []:
                market = TYPES.get(m.get("market-type"))
                if not market or m.get("status") != "open":
                    continue
                outcomes, implied = [], []
                for r in m.get("runners") or []:
                    dec = _back(r)
                    side = self._side(market, r.get("name") or "", home, away)
                    if dec is None or dec <= 1 or not side or r.get("status") != "open":
                        continue
                    line = None if market == "moneyline" else float(r["handicap"])
                    outcomes.append(Outcome(side=side, price_american=decimal_to_american(dec), line=line))
                    implied.append(1 / dec)
                if len(outcomes) != 2 or outcomes[0].side == outcomes[1].side or sum(implied) > MAX_EXCHANGE_OVERROUND:
                    continue
                gap = abs(implied[0] - implied[1])
                if market not in best or gap < best[market][0]:
                    best[market] = (gap, outcomes)
            for market in ("moneyline", "spread", "total"):
                if market in best:
                    game.markets.append(BookMarket(market=market, outcomes=best[market][1]))
            if game.markets:
                games.append(game)
        return games

    @staticmethod
    def _side(market: str, name: str, home: str, away: str) -> str | None:
        if market == "total":
            word = name.split(" ", 1)[0].upper()
            return {"OVER": "over", "UNDER": "under"}.get(word)
        if name == home or name.startswith(home + " "):
            return "home"
        if name == away or name.startswith(away + " "):
            return "away"
        return None
