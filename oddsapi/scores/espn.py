"""Schedules and scores from ESPN's public scoreboard JSON (no key).

ESPN's site terms restrict commercial reuse; this is a prototype source, not the
production score license.
"""

import re
from datetime import date, datetime, timedelta, timezone

from ..adapters.base import parse_ts
from ..models import ScoreGame, ScoreTeam

PATHS = {"nba": "basketball/nba", "nfl": "football/nfl", "mlb": "baseball/mlb"}
URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"

STATUS_BY_NAME = {
    "STATUS_POSTPONED": "postponed",
    "STATUS_SUSPENDED": "postponed",
    "STATUS_CANCELED": "canceled",
    "STATUS_CANCELLED": "canceled",
    "STATUS_FORFEIT": "final",
}
STATUS_BY_STATE = {"pre": "scheduled", "in": "live", "post": "final"}
INNING_HALF = {"top": "top", "bot": "bottom", "bottom": "bottom", "mid": "mid", "end": "end"}


class EspnScores:
    source = "espn"

    def __init__(self, client):
        self.client = client

    def fetch(self, league: str, start: date, end: date | None = None) -> list[ScoreGame]:
        # The scoreboard rejects date ranges (HTTP 400), so this is one request per day.
        games: dict[str, ScoreGame] = {}
        day = start
        while day <= (end or start):
            raw = self.client.get_json(URL.format(path=PATHS[league]), params={"dates": day.strftime("%Y%m%d")})
            for g in parse_scoreboard(league, raw, datetime.now(timezone.utc)):
                games[g.external_id] = g
            day += timedelta(days=1)
        return list(games.values())


def _team(c: dict) -> ScoreTeam:
    t = c["team"]
    return ScoreTeam(
        external_id=str(t["id"]),
        name=t.get("displayName") or t.get("name"),
        short_name=t.get("abbreviation"),
        abbreviation=t.get("abbreviation"),
        nickname=t.get("name") or t.get("shortDisplayName") or "",
        location=t.get("location") or "",
    )


def _season_label(league: str, season: dict) -> str:
    year = int(season.get("year"))
    # ESPN labels the 2026-27 NBA season 2027; the plan labels seasons by start year.
    return str(year - 1) if league == "nba" else str(year)


def _period_detail(league: str, status: str, period: int | None, short_detail: str) -> str | None:
    if status == "final":
        return "final"
    if status != "live" or not period:
        return None
    if league == "mlb":
        m = re.match(r"\s*(top|bot|bottom|mid|end)\b", short_detail, re.I)
        half = INNING_HALF[m.group(1).lower()] if m else "top"
        return f"{half}_{period}"
    if period <= 4:
        return f"q{period}"
    return "ot" if period == 5 else f"ot{period - 4}"


def _clock(league: str, status: str, display_clock: str | None, type_name: str) -> str | None:
    if league == "mlb" or status != "live" or display_clock is None:
        return None
    if type_name in ("STATUS_HALFTIME", "STATUS_END_PERIOD") or display_clock in ("0.0", "0"):
        return "0:00"
    return display_clock


def parse_scoreboard(league: str, raw: dict, captured_at: datetime) -> list[ScoreGame]:
    games = []
    for e in raw.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        st = comp.get("status") or e.get("status") or {}
        stype = st.get("type") or {}
        status = STATUS_BY_NAME.get(stype.get("name")) or STATUS_BY_STATE.get(stype.get("state"), "scheduled")
        by_side = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in by_side or "away" not in by_side:
            continue
        home, away = by_side["home"], by_side["away"]
        has_score = status in ("live", "final")
        period = st.get("period") if has_score else None

        linescore = None
        if league == "mlb" and has_score and home.get("linescores") is not None:
            h = [int(x.get("value", 0)) for x in home.get("linescores") or []]
            a = [int(x.get("value", 0)) for x in away.get("linescores") or []]
            linescore = [
                {"inning": i + 1, "away": a[i] if i < len(a) else None, "home": h[i] if i < len(h) else None}
                for i in range(max(len(h), len(a)))
            ]

        games.append(
            ScoreGame(
                external_id=str(e["id"]),
                league=league,
                season=_season_label(league, e.get("season") or raw.get("season") or {"year": parse_ts(e["date"]).year}),
                starts_at=parse_ts(e["date"]),
                status=status,
                home=_team(home),
                away=_team(away),
                venue=(comp.get("venue") or {}).get("fullName"),
                home_score=int(home["score"]) if has_score and home.get("score") not in (None, "") else None,
                away_score=int(away["score"]) if has_score and away.get("score") not in (None, "") else None,
                period=period,
                period_detail=_period_detail(league, status, period, stype.get("shortDetail") or ""),
                clock=_clock(league, status, st.get("displayClock"), stype.get("name") or ""),
                linescore=linescore,
                captured_at=captured_at,
            )
        )
    return games
