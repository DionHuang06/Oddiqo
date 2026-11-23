import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

from .db import iso, utcnow

TIERS = {
    "sandbox": {"per_minute": 60, "per_day": 1000},
    "developer": {"per_minute": 300, "per_day": 50000},
    "production": {"per_minute": 1200, "per_day": None},
}
SCOPES = ("scores:read", "odds:read")


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class ApiKey:
    id: str
    developer_name: str
    tier: str
    scopes: set[str]
    league_id: str | None


def create_key(conn: sqlite3.Connection, developer_name: str, tier: str, scopes: list[str], league: str | None = None) -> str:
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {list(TIERS)}")
    if bad := set(scopes) - set(SCOPES):
        raise ValueError(f"unknown scopes {bad}")
    if tier == "sandbox" and not league:
        raise ValueError("sandbox keys are limited to one league; pass --league")
    raw = "sk_live_" + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO api_keys (id, developer_name, key_hash, tier, scopes, league_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), developer_name, hash_key(raw), tier, ",".join(scopes), league, iso(utcnow())),
    )
    conn.commit()
    return raw


def lookup_key(conn: sqlite3.Connection, raw: str) -> ApiKey | None:
    row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (hash_key(raw),)).fetchone()
    if not row:
        return None
    return ApiKey(row["id"], row["developer_name"], row["tier"], set(row["scopes"].split(",")), row["league_id"])


class RateLimiter:
    """Fixed per-minute and per-day windows, in process memory. Fine for one API process;
    a multi-process deployment needs a shared counter store such as Redis."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._counts: dict[tuple[str, str, int], int] = {}
        self._lock = threading.Lock()

    def hit(self, key_id: str, tier: str) -> tuple[bool, dict[str, str]]:
        limits = TIERS[tier]
        now = self._clock()
        minute, day = int(now // 60), int(now // 86400)
        with self._lock:
            m = self._counts.get((key_id, "m", minute), 0)
            d = self._counts.get((key_id, "d", day), 0)
            over_minute = m >= limits["per_minute"]
            over_day = limits["per_day"] is not None and d >= limits["per_day"]
            if not (over_minute or over_day):
                m += 1
                d += 1
                self._counts[(key_id, "m", minute)] = m
                self._counts[(key_id, "d", day)] = d
            for k in [k for k in self._counts if (k[1] == "m" and k[2] < minute) or (k[1] == "d" and k[2] < day)]:
                del self._counts[k]

        if over_day and not over_minute:
            reset = (day + 1) * 86400
            headers = {"X-RateLimit-Limit": str(limits["per_day"]), "X-RateLimit-Remaining": "0"}
        else:
            reset = (minute + 1) * 60
            headers = {
                "X-RateLimit-Limit": str(limits["per_minute"]),
                "X-RateLimit-Remaining": str(max(0, limits["per_minute"] - m)),
            }
        headers["X-RateLimit-Reset"] = str(reset)
        if over_minute or over_day:
            headers["Retry-After"] = str(max(1, int(reset - now)))
        return not (over_minute or over_day), headers
