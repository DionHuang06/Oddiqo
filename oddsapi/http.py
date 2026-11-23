"""Polite HTTP access to public pages.

One plain GET per call: an honest User-Agent, a minimum gap between requests to the
same host, and no retries inside a call. Callers turn failures into backoff through
`Backoff`. There is deliberately no cookie jar replay, header spoofing, proxy support,
or anything else meant to get past a block: a 403 is reported as `BlockedError`.
"""

import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from .config import Settings


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    """The site refused anonymous access (HTTP 401/403). Do not try to work around it."""


class RateLimitedError(FetchError):
    def __init__(self, message: str, retry_after: float | None):
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(FetchError):
    pass


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


class PoliteClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep, clock=time.monotonic):
        self._settings = settings
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            timeout=settings.http_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sleep = sleep
        self._clock = clock

    def _wait_for_host(self, host: str) -> None:
        with self._lock:
            last = self._last_request.get(host)
            if last is not None:
                wait = self._settings.host_gap_seconds - (self._clock() - last)
                if wait > 0:
                    self._sleep(wait)
            self._last_request[host] = self._clock()

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        self._wait_for_host(urlsplit(url).netloc)
        try:
            resp = self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise TransientError(f"timeout: {url}") from e
        except httpx.TransportError as e:
            raise TransientError(f"transport error: {url}: {e}") from e

        if resp.status_code in (401, 403):
            raise BlockedError(f"HTTP {resp.status_code} from {urlsplit(url).netloc}: anonymous access refused")
        if resp.status_code == 429:
            raise RateLimitedError(f"HTTP 429 from {urlsplit(url).netloc}", _retry_after_seconds(resp.headers.get("Retry-After")))
        if resp.status_code >= 500:
            raise TransientError(f"HTTP {resp.status_code} from {urlsplit(url).netloc}")
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code} from {url}")
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(f"non-JSON response from {url}") from e

    def close(self) -> None:
        self._client.close()


@dataclass
class Backoff:
    """Exponential backoff per job key (e.g. 'fanduel:nba'). Times are epoch seconds."""

    base: float
    cap: float
    blocked_base: float
    blocked_cap: float
    failures: int = 0
    blocked_failures: int = 0
    not_before: float = 0.0
    last_error: str | None = None

    def ready(self, now: float) -> bool:
        return now >= self.not_before

    def success(self) -> None:
        self.failures = 0
        self.blocked_failures = 0
        self.not_before = 0.0
        self.last_error = None

    def failure(self, err: Exception, now: float) -> float:
        self.last_error = f"{type(err).__name__}: {err}"
        if isinstance(err, BlockedError):
            delay = min(self.blocked_base * 2**self.blocked_failures, self.blocked_cap)
            self.blocked_failures += 1
        else:
            delay = min(self.base * 2**self.failures, self.cap)
            self.failures += 1
            if isinstance(err, RateLimitedError) and err.retry_after:
                delay = max(delay, err.retry_after)
        self.not_before = now + delay
        return delay
