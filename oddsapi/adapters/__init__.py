from .base import OddsAdapter, ScrapingAdapter
from .draftkings import DraftKingsAdapter
from .fanduel import FanDuelAdapter
from .pinnacle import PinnacleAdapter

ADAPTERS = {
    "fanduel": FanDuelAdapter,
    "draftkings": DraftKingsAdapter,
    "pinnacle": PinnacleAdapter,
}


def build_adapters(client, settings) -> dict[str, OddsAdapter]:
    return {book: ADAPTERS[book](client, settings) for book in settings.enabled_books if book in ADAPTERS}


__all__ = ["OddsAdapter", "ScrapingAdapter", "ADAPTERS", "build_adapters"]
