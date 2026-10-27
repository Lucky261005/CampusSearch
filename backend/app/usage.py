"""Local AI request quota for one CampusSearch installation (free-use policy).

Every request actually sent to the AI provider is recorded in the local `ai_usage` table, so the
daily count survives backend restarts. A transaction-scoped advisory lock serialises the
check-and-reserve step, so concurrent requests cannot both slip under a limit.

Accounting rules:
- A slot is reserved immediately before a request is sent. It stays counted whatever the outcome
  (answer, HTTP error, 429, 5xx, or a timeout after the request may have reached the provider).
- A reservation is released (not counted) when the request never reached the provider
  (connection could not be established).
- Nothing is reserved when no request is attempted: no API key, provider cooldown, or a local
  limit already reached.
- Each retry attempt is a separate request and needs its own slot.
- If the usage table cannot be reached, no AI request is sent (fail closed).

Settings (non-secret environment variables; defaults are deliberately conservative):
    CAMPUSSEARCH_AI_DAILY_LIMIT       requests per local calendar day   (default 10)
    CAMPUSSEARCH_AI_PER_MINUTE_LIMIT  requests per rolling 60 seconds   (default 3)
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import db

log = logging.getLogger("campussearch")

LOCK_ID = 727_100_001  # arbitrary constant for pg_advisory_xact_lock


class LocalLimitReached(Exception):
    """Message is safe to show users."""


@dataclass(frozen=True)
class Limits:
    daily: int = 10
    per_minute: int = 3


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:  # unset or empty (e.g. copied from .env.example)
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("ignoring invalid %s; using %d", name, default)
        return default
    return max(0, value)


def limits_from_env() -> Limits:
    return Limits(daily=_env_int("CAMPUSSEARCH_AI_DAILY_LIMIT", 10),
                  per_minute=_env_int("CAMPUSSEARCH_AI_PER_MINUTE_LIMIT", 3))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UsageLimiter:
    def __init__(self, db_url: str | None = None, limits: Limits | None = None,
                 clock: Callable[[], datetime] = _now) -> None:
        self.db_url = db_url or db.DATABASE_URL
        self.limits = limits or limits_from_env()
        self.clock = clock

    def _start_of_local_day(self, now: datetime) -> datetime:
        local = now.astimezone()  # the machine's own time zone: "per day" means the user's day
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def reserve(self) -> int:
        """Reserve one request slot or raise LocalLimitReached. Returns the reservation id."""
        try:
            with db.connect(self.db_url) as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
                now = self.clock()
                day_start = self._start_of_local_day(now)
                minute_ago = now - timedelta(seconds=60)
                row = conn.execute(
                    "SELECT count(*) FILTER (WHERE at >= %s) AS today, "
                    "count(*) FILTER (WHERE at > %s) AS last_minute, "
                    "min(at) FILTER (WHERE at > %s) AS oldest_in_minute FROM ai_usage",
                    (day_start, minute_ago, minute_ago)).fetchone()
                if row["today"] >= self.limits.daily:
                    raise LocalLimitReached(
                        f"this installation's daily AI limit ({self.limits.daily} requests) has been "
                        "reached; it resets at local midnight")
                if row["last_minute"] >= self.limits.per_minute:
                    wait = 60 - int((now - row["oldest_in_minute"]).total_seconds())
                    raise LocalLimitReached(
                        f"this installation's AI limit of {self.limits.per_minute} requests per minute "
                        f"has been reached; try again in about {max(1, wait)} s")
                rid = conn.execute("INSERT INTO ai_usage (at, outcome) VALUES (%s, 'reserved') RETURNING id",
                                   (now,)).fetchone()["id"]
                conn.commit()
                return rid
        except LocalLimitReached:
            raise
        except Exception as e:  # database down, table missing, ...: never call the provider unmetered
            log.warning("AI usage counter unavailable: %s", type(e).__name__)
            raise LocalLimitReached("the local AI usage counter is unavailable (is the database running?); "
                                    "AI answers are disabled") from None

    def settle(self, rid: int, outcome: str) -> None:
        """Record what happened to a request that reached (or may have reached) the provider."""
        self._execute("UPDATE ai_usage SET outcome = %s WHERE id = %s", (outcome[:40], rid))

    def release(self, rid: int) -> None:
        """Remove a reservation for a request that never reached the provider."""
        self._execute("DELETE FROM ai_usage WHERE id = %s", (rid,))

    def _execute(self, sql: str, params: tuple) -> None:
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(sql, params)
                conn.commit()
        except Exception as e:  # the reservation itself already counts; keep it conservative
            log.warning("could not update AI usage record: %s", type(e).__name__)

    def used_today(self) -> int:
        with db.connect(self.db_url) as conn:
            return conn.execute("SELECT count(*) AS n FROM ai_usage WHERE at >= %s",
                                (self._start_of_local_day(self.clock()),)).fetchone()["n"]
