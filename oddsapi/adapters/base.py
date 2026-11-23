from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..models import BookGame


class OddsAdapter(ABC):
    """One odds source for one book.

    `fetch(league)` returns current pregame moneyline, spread/run line and total for
    every game the source lists. A licensed feed can replace a scraper by implementing
    this same method; ingestion does not care where the numbers come from.
    """

    book: str
    source: str

    @abstractmethod
    def fetch(self, league: str) -> list[BookGame]: ...


class ScrapingAdapter(OddsAdapter):
    """Adapter over a book's public, logged-out JSON. Split into fetch_raw and parse so
    parsing can be tested against recorded fixtures without the network."""

    league_ids: dict[str, object]

    def __init__(self, client, settings):
        self.client = client
        self.settings = settings

    def supports(self, league: str) -> bool:
        return league in self.league_ids

    @abstractmethod
    def fetch_raw(self, league: str) -> dict: ...

    @abstractmethod
    def parse(self, league: str, raw: dict, captured_at: datetime) -> list[BookGame]: ...

    def fetch(self, league: str) -> list[BookGame]:
        raw = self.fetch_raw(league)
        return self.parse(league, raw, datetime.now(timezone.utc))


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
