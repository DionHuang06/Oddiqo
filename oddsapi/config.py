import os
from dataclasses import dataclass, field

LEAGUES = ("nba", "nfl", "mlb")
BOOKS = ("fanduel", "draftkings", "pinnacle")
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

    @property
    def effective_odds_poll_seconds(self) -> int:
        if self.allow_fast_poll:
            return self.odds_poll_seconds
        return max(self.odds_poll_seconds, MIN_ODDS_POLL_SECONDS)


def get_settings() -> Settings:
    return Settings()
