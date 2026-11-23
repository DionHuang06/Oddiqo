from .base import OddsAdapter, ScrapingAdapter
from .betrivers import BetRiversAdapter
from .bovada import BovadaAdapter
from .draftkings import DraftKingsAdapter
from .fanduel import FanDuelAdapter
from .kalshi import KalshiAdapter
from .kambi import KambiAdapter
from .matchbook import MatchbookAdapter
from .pinnacle import PinnacleAdapter
from .polymarket import PolymarketAdapter
from .smarkets import SmarketsAdapter

ADAPTERS = {
    "fanduel": FanDuelAdapter,
    "draftkings": DraftKingsAdapter,
    "pinnacle": PinnacleAdapter,
    "betrivers": BetRiversAdapter,
    "unibet": KambiAdapter,
    "bovada": BovadaAdapter,
    "matchbook": MatchbookAdapter,
    "smarkets": SmarketsAdapter,
    "kalshi": KalshiAdapter,
    "polymarket": PolymarketAdapter,
}


def build_adapters(client, settings) -> dict[str, OddsAdapter]:
    return {book: ADAPTERS[book](client, settings) for book in settings.enabled_books if book in ADAPTERS}


__all__ = ["OddsAdapter", "ScrapingAdapter", "ADAPTERS", "build_adapters"]
