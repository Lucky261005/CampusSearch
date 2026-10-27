"""Local AI quota (free-use policy). Uses the isolated test database and mocked HTTP only."""

import logging
import threading
from datetime import datetime, timedelta

import httpx
import pytest

from app import rag, usage
from app.rag import LLMUnavailable, groq_chat
from app.usage import Limits, LocalLimitReached, UsageLimiter

KEY = "test-key-must-not-leak"
MSGS = [{"role": "user", "content": "hi"}]
NOON = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)


class Clock:
    def __init__(self, at=NOON):
        self.at = at

    def __call__(self):
        return self.at

    def advance(self, **kw):
        self.at += timedelta(**kw)


@pytest.fixture
def quota_db(conn, _test_db):
    conn.execute("DELETE FROM ai_usage")
    conn.commit()
    return _test_db


def make(quota_db, daily=10, per_minute=3, clock=None):
    return UsageLimiter(quota_db, Limits(daily, per_minute), clock or Clock())


def rows(conn):
    return conn.execute("SELECT outcome FROM ai_usage ORDER BY id").fetchall()


# --- the limiter ------------------------------------------------------------------------------

def test_defaults_are_conservative(monkeypatch):
    for name in ("CAMPUSSEARCH_AI_DAILY_LIMIT", "CAMPUSSEARCH_AI_PER_MINUTE_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    assert usage.limits_from_env() == Limits(daily=10, per_minute=3)
    assert rag.GROQ_MAX_TOKENS == 600


def test_settings_are_configurable_and_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("CAMPUSSEARCH_AI_DAILY_LIMIT", "5")
    monkeypatch.setenv("CAMPUSSEARCH_AI_PER_MINUTE_LIMIT", "lots")
    assert usage.limits_from_env() == Limits(daily=5, per_minute=3)


@pytest.mark.parametrize("raw, expected", [("", 600), (" ", 600), ("abc", 600), ("-5", 0), ("250", 250)])
def test_empty_or_invalid_numeric_settings_fall_back(monkeypatch, raw, expected):
    # values copied verbatim from .env.example must never crash or disable the defaults
    monkeypatch.setenv("CAMPUSSEARCH_AI_MAX_TOKENS", raw)
    assert usage._env_int("CAMPUSSEARCH_AI_MAX_TOKENS", 600) == expected


def test_daily_limit(quota_db):
    clock = Clock()
    lim = make(quota_db, daily=10, per_minute=100, clock=clock)
    for _ in range(10):
        lim.reserve()
        clock.advance(seconds=1)
    with pytest.raises(LocalLimitReached, match=r"daily AI limit \(10 requests\).*local midnight"):
        lim.reserve()


def test_minute_limit_is_rolling(quota_db):
    clock = Clock()
    lim = make(quota_db, per_minute=3, clock=clock)
    for _ in range(3):
        lim.reserve()
    clock.advance(seconds=20)
    with pytest.raises(LocalLimitReached, match=r"3 requests per minute.*about 40 s"):
        lim.reserve()
    clock.advance(seconds=41)  # the first three are now older than 60 s
    lim.reserve()


def test_daily_count_persists_across_restart_and_resets_next_day(quota_db):
    clock = Clock()
    make(quota_db, daily=5, per_minute=100, clock=clock).reserve()
    for _ in range(3):
        make(quota_db, daily=5, per_minute=100, clock=clock).reserve()  # a fresh limiter per "restart"
    restarted = make(quota_db, daily=5, per_minute=100, clock=clock)
    assert restarted.used_today() == 4
    restarted.reserve()
    with pytest.raises(LocalLimitReached, match="daily"):
        restarted.reserve()
    clock.advance(days=1)  # next local day
    assert restarted.used_today() == 0
    restarted.reserve()


def test_concurrent_requests_cannot_bypass_the_limit(quota_db, conn):
    lim = make(quota_db, daily=10, per_minute=3)
    start, results = threading.Barrier(12), []

    def worker():
        start.wait()
        try:
            lim.reserve()
            results.append("ok")
        except LocalLimitReached:
            results.append("limited")

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 3 and results.count("limited") == 9
    assert len(rows(conn)) == 3


def test_release_uncounts_and_settle_records_outcome(quota_db, conn):
    lim = make(quota_db)
    a, b = lim.reserve(), lim.reserve()
    lim.release(a)
    lim.settle(b, "http_200")
    assert [r["outcome"] for r in rows(conn)] == ["http_200"]


def test_unreachable_counter_fails_closed(caplog):
    caplog.set_level(logging.WARNING, logger="campussearch")
    lim = UsageLimiter("postgresql://campus:secretpw@127.0.0.1:1/none", Limits())
    with pytest.raises(LocalLimitReached, match="usage counter is unavailable"):
        lim.reserve()
    assert "secretpw" not in caplog.text


# --- the provider with the real quota ---------------------------------------------------------

@pytest.fixture
def provider(quota_db, monkeypatch):
    """groq_chat with the real quota table and a queue of mocked HTTP outcomes."""
    clock = Clock()
    limiter = make(quota_db, daily=10, per_minute=3, clock=clock)
    monkeypatch.setattr(rag, "_limiter", limiter)
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    queue, calls = [], []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(rag.httpx, "post", fake_post)
    return limiter, clock, queue, calls


def http(status, body=None):
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", rag.GROQ_URL))


OK = http(200, {"choices": [{"message": {"content": '{"answer": "", "citations": [], '
                                                    '"insufficient_evidence": true}'}}]})


def test_successful_request_is_counted_with_600_token_cap(provider, conn):
    _, _, queue, calls = provider
    queue.append(OK)
    groq_chat(MSGS)
    assert [r["outcome"] for r in rows(conn)] == ["http_200"]
    assert calls[0]["max_tokens"] == 600


def test_missing_key_reserves_nothing(provider, conn, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(LLMUnavailable, match="not configured"):
        groq_chat(MSGS)
    assert rows(conn) == []


def test_429_counts_once_starts_cooldown_and_is_not_retried(provider, conn):
    _, _, queue, calls = provider
    queue.append(http(429, {"error": {"code": "rate_limit_exceeded"}}))
    with pytest.raises(LLMUnavailable, match="paused"):
        groq_chat(MSGS)
    with pytest.raises(LLMUnavailable, match="resume in about") as e:  # cooldown: nothing sent
        groq_chat(MSGS)
    assert e.value.attempted is False and len(calls) == 1
    assert [r["outcome"] for r in rows(conn)] == ["http_429"]


def test_connection_refused_is_not_counted(provider, conn):
    _, _, queue, _ = provider
    queue.extend([httpx.ConnectError("refused"), httpx.ConnectError("refused")])
    with pytest.raises(LLMUnavailable, match="could not reach"):
        groq_chat(MSGS)
    assert rows(conn) == []


def test_timeout_counts_because_the_provider_may_have_received_it(provider, conn):
    _, _, queue, _ = provider
    queue.extend([httpx.ReadTimeout("slow"), OK])
    groq_chat(MSGS)
    assert [r["outcome"] for r in rows(conn)] == ["ReadTimeout", "http_200"]


def test_local_limit_blocks_before_any_request(provider, conn):
    limiter, _, queue, calls = provider
    for _ in range(3):
        limiter.reserve()
    with pytest.raises(LLMUnavailable, match="3 requests per minute") as e:
        groq_chat(MSGS)
    assert e.value.attempted is False and calls == [] and len(rows(conn)) == 3


def test_daily_limit_message_reaches_the_user(provider):
    limiter, clock, _, _ = provider
    limiter.limits = Limits(daily=1, per_minute=3)
    limiter.reserve()
    clock.advance(minutes=5)
    with pytest.raises(LLMUnavailable, match=r"daily AI limit \(1 requests\)"):
        groq_chat(MSGS)


def test_retry_needs_its_own_slot(provider, conn):
    limiter, _, queue, calls = provider
    limiter.limits = Limits(daily=10, per_minute=1)
    queue.extend([http(503), OK])
    with pytest.raises(LLMUnavailable, match="HTTP 503"):
        groq_chat(MSGS)  # the retry was not sent: the minute quota was used by the first attempt
    assert len(calls) == 1 and [r["outcome"] for r in rows(conn)] == ["http_503"]


def test_no_secret_in_logs_or_errors(provider, caplog):
    caplog.set_level(logging.DEBUG)
    _, _, queue, _ = provider
    queue.append(http(401, {"error": {"code": "invalid_api_key", "message": f"bad key {KEY}"}}))
    with pytest.raises(LLMUnavailable) as e:
        groq_chat(MSGS)
    assert KEY not in str(e.value) and KEY not in caplog.text
