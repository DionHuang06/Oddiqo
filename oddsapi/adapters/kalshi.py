import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..models import BookGame, BookMarket, Outcome
from ..odds_math import prob_to_american
from .base import MAX_EXCHANGE_OVERROUND, ScrapingAdapter

ET = ZoneInfo("America/New_York")
API = "https://api.elections.kalshi.com/trade-api/v2/events"
SERIES = {"moneyline": "KXNBAGAME", "spread": "KXNBASPREAD", "total": "KXNBATOTAL"}
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
# Kalshi team codes that differ from ESPN abbreviations.
TO_ESPN = {"GSW": "GS", "NYK": "NY", "SAS": "SA", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH"}
EVENT_RE = re.compile(r"^KXNBA\w+-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _event_key(ticker: str):
    m = EVENT_RE.match(ticker)
    if not m:
        return None
    yy, mon, dd, away, home = m.groups()
    if mon not in MONTHS:
        return None
    return date(2000 + int(yy), MONTHS[mon], int(dd)), away, home


def _balanced(markets: list[dict]) -> dict | None:
    """The strike whose yes contract trades closest to 50 cents: Kalshi's equivalent of a main line."""
    best, best_gap = None, None
    for m in markets:
        bid, ask, no_ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("no_ask_dollars"))
        if m.get("status") != "active" or not bid or not ask or not no_ask or m.get("floor_strike") is None:
            continue
        if ask + no_ask > MAX_EXCHANGE_OVERROUND:
            continue
        gap = abs((bid + ask) / 2 - 0.5)
        if best_gap is None or gap < best_gap:
            best, best_gap = m, gap
    return best


class KalshiAdapter(ScrapingAdapter):
    """Kalshi (CFTC-regulated exchange) public market-data API; no account needed for reads.
    Prices are the current ask for a $1 contract, converted to American odds before fees.
    Tickers carry only the game date, so games match on teams + ET date."""

    book = "kalshi"
    source = "kalshi_api"
    league_ids = {"nba": "nba"}

    def fetch_raw(self, league: str) -> dict:
        out = {}
        for market, series in SERIES.items():
            events, cursor = [], None
            for _ in range(5):
                params = {"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                page = self.client.get_json(API, params=params)
                events += page.get("events") or []
                cursor = page.get("cursor")
                if not cursor:
                    break
            out[market] = events
        return out

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        games: dict[tuple, BookGame] = {}
        for market, events in raw.items():
            for ev in events:
                key = _event_key(ev.get("event_ticker", ""))
                if not key:
                    continue
                day, away, home = key
                outcomes = self._outcomes(market, ev.get("markets") or [], away, home)
                if not outcomes:
                    continue
                game = games.get(key)
                if game is None:
                    game = games[key] = BookGame(
                        external_id=f"{day.isoformat()}-{away}-{home}",
                        league=league,
                        starts_at=datetime.combine(day, time(12), ET),
                        home_name=TO_ESPN.get(home, home),
                        away_name=TO_ESPN.get(away, away),
                        captured_at=captured_at,
                        date_only=True,
                    )
                game.markets.append(BookMarket(market=market, outcomes=outcomes))
        return list(games.values())

    def _outcomes(self, market: str, markets: list[dict], away: str, home: str) -> list[Outcome] | None:
        if market == "moneyline":
            by_side, cost = {}, 0.0
            for m in markets:
                code = m.get("ticker", "").rsplit("-", 1)[-1]
                side = "home" if code == home else "away" if code == away else None
                price = prob_to_american(_f(m.get("yes_ask_dollars")) or 0)
                if side and m.get("status") == "active" and price is not None:
                    by_side[side] = Outcome(side=side, price_american=price)
                    cost += _f(m.get("yes_ask_dollars"))
            if len(by_side) != 2 or cost > MAX_EXCHANGE_OVERROUND:
                return None
            return [by_side["away"], by_side["home"]]

        m = _balanced(markets)
        if m is None:
            return None
        yes = prob_to_american(_f(m["yes_ask_dollars"]))
        no = prob_to_american(_f(m["no_ask_dollars"]))
        if yes is None or no is None:
            return None
        strike = float(m["floor_strike"])
        if market == "total":
            return [Outcome("over", yes, strike), Outcome("under", no, strike)]
        code = re.sub(r"\d+$", "", m.get("ticker", "").rsplit("-", 1)[-1])
        if code not in (home, away):
            return None
        fav = "home" if code == home else "away"
        dog = "away" if fav == "home" else "home"
        return [Outcome(fav, yes, -strike), Outcome(dog, no, strike)]
