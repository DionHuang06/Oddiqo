from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Outcome:
    side: str  # "home" | "away" | "over" | "under"
    price_american: int
    line: float | None = None


@dataclass
class BookMarket:
    market: str  # "moneyline" | "spread" | "total"
    outcomes: list[Outcome]


@dataclass
class BookGame:
    """One pregame event as a single odds source reports it, before matching to a Game."""

    external_id: str
    league: str
    starts_at: datetime
    home_name: str
    away_name: str
    markets: list[BookMarket] = field(default_factory=list)
    captured_at: datetime | None = None


@dataclass
class ScoreTeam:
    external_id: str
    name: str
    short_name: str
    abbreviation: str
    nickname: str
    location: str


@dataclass
class ScoreGame:
    external_id: str
    league: str
    season: str
    starts_at: datetime
    status: str  # scheduled | live | final | postponed | canceled
    home: ScoreTeam
    away: ScoreTeam
    venue: str | None
    home_score: int | None
    away_score: int | None
    period: int | None
    period_detail: str | None
    clock: str | None
    linescore: list[dict] | None
    captured_at: datetime
