from datetime import datetime

from ..models import BookGame, BookMarket, Outcome
from .base import ScrapingAdapter, parse_ts

# Market types on FanDuel's league pages. NBA/NFL names are confirmed from recorded
# payloads; the MLB variants were not on the board when fixtures were recorded
# (offseason) and are best guesses.
MARKET_TYPES = {
    "MONEY_LINE": "moneyline",
    "MATCH_HANDICAP_(2-WAY)": "spread",
    "RUN_LINE": "spread",
    "TOTAL_POINTS_(OVER/UNDER)": "total",
    "TOTAL_RUNS_(OVER/UNDER)": "total",
}
SIDES = {"HOME": "home", "AWAY": "away", "OVER": "over", "UNDER": "under"}


class FanDuelAdapter(ScrapingAdapter):
    """Reads the content API that sportsbook.fanduel.com's own league pages call."""

    book = "fanduel"
    source = "fanduel_web"
    league_ids = {"nba": "nba", "nfl": "nfl", "mlb": "mlb"}

    def url(self) -> str:
        state = self.settings.fanduel_state
        tld = "ca" if state == "on" else "com"
        return f"https://sbapi.{state}.sportsbook.fanduel.{tld}/api/content-managed-page"

    def fetch_raw(self, league: str) -> dict:
        return self.client.get_json(
            self.url(),
            params={
                "page": "CUSTOM",
                "customPageId": self.league_ids[league],
                "_ak": self.settings.fanduel_app_key,
                "timezone": "America/New_York",
            },
        )

    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]:
        attachments = raw.get("attachments") or {}
        events = attachments.get("events") or {}
        markets = attachments.get("markets") or {}

        games: dict[int, BookGame] = {}
        seen: set[tuple[int, str]] = set()
        for m in markets.values():
            market = MARKET_TYPES.get(m.get("marketType"))
            if not market or m.get("inPlay") or m.get("marketStatus") != "OPEN":
                continue
            event_id = m.get("eventId")
            event = events.get(str(event_id))
            if not event or (event_id, market) in seen:
                continue

            outcomes, names = [], {}
            for r in m.get("runners") or []:
                side = SIDES.get((r.get("result") or {}).get("type"))
                if not side or r.get("runnerStatus") != "ACTIVE":
                    continue
                odds = ((r.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
                if odds is None:
                    continue
                line = None if market == "moneyline" else float(r.get("handicap"))
                outcomes.append(Outcome(side=side, price_american=int(odds), line=line))
                if side in ("home", "away"):
                    names[side] = r.get("runnerName")
            if len(outcomes) != 2:
                continue

            game = games.get(event_id)
            if game is None:
                home, away = names.get("home"), names.get("away")
                if not (home and away):
                    home, away = _split_event_name(event.get("name", ""))
                if not (home and away):
                    continue
                # NBA 2K esports matches share the league page: "Boston Celtics (COMMANDER)".
                if "(" in home or "(" in away:
                    continue
                game = BookGame(
                    external_id=str(event_id),
                    league=league,
                    starts_at=parse_ts(event["openDate"]),
                    home_name=home,
                    away_name=away,
                    captured_at=captured_at,
                )
                games[event_id] = game
            game.markets.append(BookMarket(market=market, outcomes=outcomes))
            seen.add((event_id, market))
        return list(games.values())


def _split_event_name(name: str) -> tuple[str | None, str | None]:
    if " @ " in name:
        away, home = name.split(" @ ", 1)
        return home.strip(), away.strip()
    return None, None
