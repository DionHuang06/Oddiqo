import re
from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import prob_to_american
from .base import MAX_EXCHANGE_OVERROUND, ScrapingAdapter, parse_ts

API = "https://api.smarkets.com/v3"
SIDES = {"HOME": "home", "AWAY": "away", "OVER": "over", "UNDER": "under"}
LINE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)(?: points)?$")
BATCH = 100


def _market_kind(m: dict) -> str | None:
    kind = (m.get("market_type") or {}).get("name")
    name = m.get("name") or ""
    if kind == "WINNER_2_WAY" and name == "Winner (including overtime)":
        return "moneyline"
    if kind == "HANDICAP" and name.endswith("(including overtime)"):
        return "spread"
    if kind == "OVER_UNDER" and name.startswith("Over/under") and name.endswith("(including overtime)"):
        return "total"
    return None


class SmarketsAdapter(ScrapingAdapter):
    """Smarkets exchange's public v3 API (the JSON smarkets.com reads logged out). Prices are
    the best offer (what a backer pays) in basis points of probability, before commission.
    Needs four calls per poll: events, markets, contracts, quotes (the last two batched)."""

    book = "smarkets"
    source = "smarkets_api"
    league_ids = {"nba": "/sport/basketball/nba/"}

    def fetch_raw(self, league: str) -> dict:
        events = self.client.get_json(
            f"{API}/events/",
            params={"type_domain": "basketball", "type_scope": "single_event", "state": "upcoming", "limit": 200, "sort": "start_datetime,id"},
        ).get("events") or []
        events = [e for e in events if self.league_ids[league] in (e.get("full_slug") or "")]
        if not events:
            return {"events": [], "markets": [], "contracts": [], "quotes": {}}
        markets = self.client.get_json(f"{API}/events/{','.join(e['id'] for e in events)}/markets/").get("markets") or []
        markets = [m for m in markets if _market_kind(m) and m.get("state") == "open"]
        contracts, quotes = [], {}
        ids = [m["id"] for m in markets]
        for i in range(0, len(ids), BATCH):
            chunk = ",".join(ids[i : i + BATCH])
            contracts += self.client.get_json(f"{API}/markets/{chunk}/contracts/").get("contracts") or []
            quotes.update(self.client.get_json(f"{API}/markets/{chunk}/quotes/"))
        return {"events": events, "markets": markets, "contracts": contracts, "quotes": quotes}

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        quotes = raw.get("quotes") or {}
        by_market: dict[str, list] = {}
        for c in raw.get("contracts") or []:
            side = SIDES.get((c.get("contract_type") or {}).get("name"))
            offers = (quotes.get(str(c["id"])) or {}).get("offers") or []
            if side and offers:
                by_market.setdefault(str(c["market_id"]), []).append((side, offers[0]["price"] / 10000, c.get("name") or ""))

        best: dict[tuple[str, str], tuple[float, list[Outcome]]] = {}
        for m in raw.get("markets") or []:
            kind = _market_kind(m)
            cs = by_market.get(str(m["id"]), [])
            if not kind or len(cs) != 2 or cs[0][0] == cs[1][0] or cs[0][1] + cs[1][1] > MAX_EXCHANGE_OVERROUND:
                continue
            outcomes = []
            for side, p, name in cs:
                price = prob_to_american(p)
                line = None
                if kind != "moneyline":
                    lm = LINE_RE.search(name)
                    line = float(lm.group(1)) if lm else None
                if price is None or (kind != "moneyline" and line is None):
                    break
                outcomes.append(Outcome(side=side, price_american=price, line=line))
            else:
                gap = abs(cs[0][1] - cs[1][1])
                key = (str(m["event_id"]), kind)
                if key not in best or gap < best[key][0]:
                    order = ("away", "home") if kind != "total" else ("over", "under")
                    best[key] = (gap, sorted(outcomes, key=lambda o: order.index(o.side)))

        games = []
        for ev in raw.get("events") or []:
            away, sep, home = (ev.get("name") or "").partition(" at ")
            if not sep:
                continue
            starts = parse_ts(ev["start_datetime"])
            if starts <= captured_at:
                continue
            game = BookGame(external_id=str(ev["id"]), league=league, starts_at=starts, home_name=home, away_name=away, captured_at=captured_at)
            for kind in ("moneyline", "spread", "total"):
                if (str(ev["id"]), kind) in best:
                    game.markets.append(BookMarket(market=kind, outcomes=best[(str(ev["id"]), kind)][1]))
            if game.markets:
                games.append(game)
        return games
