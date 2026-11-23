import os
from dataclasses import dataclass, field

LEAGUES = ("nba", "nfl", "mlb")
BOOKS = (
    "fanduel",
    "draftkings",
    "pinnacle",
    "betrivers",
    "unibet",
    "bovada",
    "matchbook",
    "smarkets",
    "kalshi",
    "polymarket",
)
BOOK_NAMES = {
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
    "pinnacle": "Pinnacle",
    "betrivers": "BetRivers",
    "unibet": "Unibet",
    "bovada": "Bovada",
    "matchbook": "Matchbook",
    "smarkets": "Smarkets",
    "kalshi": "Kalshi",
    "polymarket": "Polymarket",
}
MARKETS = ("moneyline", "spread", "total")

MIN_ODDS_POLL_SECONDS = 60


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("ODDSAPI_DB", "oddsapi.sqlite3"))
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "ODDSAPI_USER_AGENT",
            "sportsbook-odds-api-prototype/0.1 (+contact: "
            + os.environ.get("ODDSAPI_CONTACT", "set ODDSAPI_CONTACT")
            + ")",
        )
    )
    enabled_books: tuple[str, ...] = field(default_factory=lambda: _env_list("ODDSAPI_BOOKS", ",".join(BOOKS)))
    enabled_leagues: tuple[str, ...] = field(default_factory=lambda: _env_list("ODDSAPI_LEAGUES", ",".join(LEAGUES)))
    # Per book per league. Values under MIN_ODDS_POLL_SECONDS are raised to it unless
    # ODDSAPI_ALLOW_FAST_POLL=1 is set explicitly.
    odds_poll_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_ODDS_POLL_SECONDS", 60))
    allow_fast_poll: bool = field(default_factory=lambda: os.environ.get("ODDSAPI_ALLOW_FAST_POLL") == "1")
    # Minimum gap between any two requests to the same host.
    host_gap_seconds: float = field(default_factory=lambda: float(os.environ.get("ODDSAPI_HOST_GAP_SECONDS", "3")))
    http_timeout_seconds: float = 20.0
    backoff_base_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_BACKOFF_BASE_SECONDS", 120))
    backoff_max_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_BACKOFF_MAX_SECONDS", 3600))
    blocked_backoff_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_BLOCKED_BACKOFF_SECONDS", 6 * 3600))
    blocked_backoff_max_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_BLOCKED_BACKOFF_MAX_SECONDS", 24 * 3600))

    schedule_poll_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_SCHEDULE_POLL_SECONDS", 6 * 3600))
    schedule_days_ahead: int = 14
    live_score_poll_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_LIVE_SCORE_POLL_SECONDS", 30))
    soon_score_poll_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_SOON_SCORE_POLL_SECONDS", 300))
    # Skip odds polls for a league with no scheduled games in this window (e.g. MLB offseason).
    odds_lookahead_hours: int = 72

    match_tolerance_minutes: int = field(default_factory=lambda: _env_int("ODDSAPI_MATCH_TOLERANCE_MINUTES", 15))
    odds_stale_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_ODDS_STALE_SECONDS", 180))
    odds_max_age_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_ODDS_MAX_AGE_SECONDS", 600))
    live_score_stale_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_LIVE_SCORE_STALE_SECONDS", 120))

    fanduel_state: str = field(default_factory=lambda: os.environ.get("ODDSAPI_FANDUEL_STATE", "nj"))
    # Public application key that sportsbook.fanduel.com's own pages send to its content API.
    fanduel_app_key: str = field(default_factory=lambda: os.environ.get("ODDSAPI_FANDUEL_AK", "FhMFpcPWXMeyZxOx"))
    draftkings_site: str = field(default_factory=lambda: os.environ.get("ODDSAPI_DRAFTKINGS_SITE", "dkusnj"))
    # Ontario: ODDSAPI_BETRIVERS_HOST=on.betrivers.ca ODDSAPI_BETRIVERS_CAGE=249
    betrivers_host: str = field(default_factory=lambda: os.environ.get("ODDSAPI_BETRIVERS_HOST", "nj.betrivers.com"))
    betrivers_cage: str = field(default_factory=lambda: os.environ.get("ODDSAPI_BETRIVERS_CAGE", "2"))
    # Kambi offering code and market for Unibet: "ubca"/"CA" is the Canadian (Ontario) site.
    kambi_offering: str = field(default_factory=lambda: os.environ.get("ODDSAPI_KAMBI_OFFERING", "ubca"))
    kambi_market: str = field(default_factory=lambda: os.environ.get("ODDSAPI_KAMBI_MARKET", "CA"))

    # NBA day-slate scheduler: every slate_refresh_seconds, reload today's (ET) NBA games
    # from ESPN; poll each book's NBA odds every nba_odds_poll_seconds while any of
    # those games is still pregame. slate_days=2 also covers tomorrow.
    slate_refresh_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_SLATE_REFRESH_SECONDS", 1800))
    nba_odds_poll_seconds: int = field(default_factory=lambda: _env_int("ODDSAPI_NBA_ODDS_POLL_SECONDS", 1800))
    slate_days: int = field(default_factory=lambda: _env_int("ODDSAPI_SLATE_DAYS", 1))
    slate_leagues: tuple[str, ...] = field(default_factory=lambda: _env_list("ODDSAPI_SLATE_LEAGUES", "nba"))

    @property
    def effective_odds_poll_seconds(self) -> int:
        return self._floor(self.odds_poll_seconds)

    def _floor(self, seconds: int) -> int:
        return seconds if self.allow_fast_poll else max(seconds, MIN_ODDS_POLL_SECONDS)

    def odds_poll_for(self, league: str) -> int:
        if league in self.slate_leagues:
            return self._floor(self.nba_odds_poll_seconds)
        return self.effective_odds_poll_seconds

    def odds_stale_for(self, league: str) -> int:
        """A price is stale once it misses about one poll, so slow schedules aren't flagged early."""
        return max(self.odds_stale_seconds, int(self.odds_poll_for(league) * 1.5))

    def odds_max_age_for(self, league: str) -> int:
        return max(self.odds_max_age_seconds, int(self.odds_poll_for(league) * 2.5))


def get_settings() -> Settings:
    return Settings()
