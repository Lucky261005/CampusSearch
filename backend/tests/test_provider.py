"""Groq provider failure modes (Phase 7). All responses are mocked; no request leaves the machine."""

import json
import logging

import httpx
import pytest

from app import rag
from app.rag import LLMUnavailable, answer, groq_chat
from test_rag import PS, reply

KEY = "test-key-should-never-appear"
MSGS = [{"role": "user", "content": "hi"}]


def response(status, body=None, text=None):
    req = httpx.Request("POST", rag.GROQ_URL)
    if text is not None:
        return httpx.Response(status, text=text, request=req)
    return httpx.Response(status, json=body if body is not None else {}, request=req)


def ok(content='{"answer": "", "citations": [], "insufficient_evidence": true}'):
    return response(200, {"choices": [{"message": {"content": content}}]})


@pytest.fixture
def post(monkeypatch):
    """Queue of responses/exceptions returned by successive httpx.post calls."""
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    queue, calls = [], []

    def fake(url, headers, json, timeout):
        calls.append(json)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(rag.httpx, "post", fake)
    fake.queue, fake.calls = queue, calls
    return fake


def fails_with(post, items, match):
    post.queue.extend(items)
    with pytest.raises(LLMUnavailable, match=match) as e:
        groq_chat(MSGS)
    assert KEY not in str(e.value)
    return len(post.calls)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="not configured") as e:
        groq_chat(MSGS)
    assert e.value.attempted is False


def test_missing_key_is_reported_as_no_call(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    info = {"mode": "hybrid"}
    res = answer("How are hostel rooms given?", PS, groq_chat, info)
    assert res.status == "fallback" and info["llm_called"] is False


def test_invalid_key_is_not_retried(post):
    calls = fails_with(post, [response(401, {"error": {"code": "invalid_api_key",
                                                         "message": f"Invalid API Key {KEY}"}})],
                       "rejected the API key; check GROQ_API_KEY")
    assert calls == 1


def test_rate_limit_is_not_retried(post):
    assert fails_with(post, [response(429, {"error": {"code": "rate_limit_exceeded"}})],
                      "rate limit or quota") == 1


def limited(retry_after=None):
    res = response(429, {"error": {"code": "rate_limit_exceeded"}})
    if retry_after is not None:
        res.headers["retry-after"] = retry_after
    return res


def test_after_429_no_request_is_sent_until_retry_after(post, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rag.time, "monotonic", lambda: clock[0])
    fails_with(post, [limited("30")], "paused for about 30 s")
    for _ in range(3):  # further questions during the pause send nothing
        with pytest.raises(LLMUnavailable, match="resume in about") as e:
            groq_chat(MSGS)
        assert e.value.attempted is False
    assert len(post.calls) == 1
    clock[0] += 31
    post.queue.append(ok())
    groq_chat(MSGS)  # pause over: one new request
    assert len(post.calls) == 2


@pytest.mark.parametrize("header, expected", [(None, 60), ("2", 10), ("99999", 600), ("soon", 60)])
def test_cooldown_is_bounded(post, monkeypatch, header, expected):
    monkeypatch.setattr(rag.time, "monotonic", lambda: 0.0)
    fails_with(post, [limited(header)], f"paused for about {expected} s")
    assert rag._cooldown_until == expected


def test_paused_provider_is_reported_as_no_call(post, monkeypatch):
    monkeypatch.setattr(rag, "_cooldown_until", rag.time.monotonic() + 100)
    info = {"mode": "hybrid"}
    res = answer("How are hostel rooms given?", PS, groq_chat, info)
    assert res.status == "fallback" and "resume in about" in res.message
    assert info["llm_called"] is False and post.calls == []


def test_blocked_account_explains_the_limit(post):
    fails_with(post, [response(400, {"error": {"code": "blocked_api_access"}})],
               "usage or spending limit")


@pytest.mark.parametrize("res", [
    response(404, {"error": {"code": "model_not_found"}}),
    response(400, {"error": {"code": "model_decommissioned"}}),
])
def test_unsupported_model_names_the_setting(post, res):
    assert fails_with(post, [res], f"model '{rag.GROQ_MODEL}' is not available; set GROQ_MODEL") == 1


def test_other_client_error_is_reported_without_details(post):
    fails_with(post, [response(400, {"error": {"code": "invalid_request", "message": "secret detail"}})],
               r"failed \(HTTP 400\)")


def test_timeout_is_retried_once_then_succeeds(post):
    post.queue.extend([httpx.ReadTimeout("slow"), ok()])
    assert json.loads(groq_chat(MSGS))["insufficient_evidence"] is True
    assert len(post.calls) == 2


@pytest.mark.parametrize("first", [httpx.ConnectError("refused"), response(503)])
def test_transient_failures_give_up_after_bounded_attempts(post, first):
    calls = fails_with(post, [first, first], "could not reach|HTTP 503")
    assert calls == rag.GROQ_ATTEMPTS == 2


@pytest.mark.parametrize("res, match", [
    (response(200, {"unexpected": True}), "unexpected response"),
    (response(200, text="<html>gateway</html>"), "unexpected response"),
    (response(200, {"choices": []}), "unexpected response"),
    (ok(""), "empty answer"),
    (ok("   "), "empty answer"),
    (response(200, {"choices": [{"message": {"content": None}}]}), "empty answer"),
])
def test_malformed_or_empty_replies(post, res, match):
    fails_with(post, [res], match)


def test_request_payload_and_reasoning_setting(post, monkeypatch):
    post.queue.append(ok())
    monkeypatch.setattr(rag, "GROQ_MODEL", "openai/gpt-oss-120b")
    groq_chat(MSGS)
    sent = post.calls[0]
    assert sent["model"] == "openai/gpt-oss-120b" and sent["temperature"] == 0
    assert sent["max_tokens"] == rag.GROQ_MAX_TOKENS and sent["reasoning_effort"] == "low"
    assert sent["response_format"] == {"type": "json_object"}
    post.queue.append(ok())
    monkeypatch.setattr(rag, "GROQ_MODEL", "some-other-model")
    groq_chat(MSGS)
    assert "reasoning_effort" not in post.calls[1]


def test_logs_never_contain_the_key_or_provider_message(post, caplog):
    caplog.set_level(logging.WARNING, logger="campussearch")
    post.queue.append(response(401, {"error": {"code": "invalid_api_key", "message": f"bad {KEY}"}}))
    with pytest.raises(LLMUnavailable):
        groq_chat(MSGS)
    assert caplog.records and KEY not in caplog.text and "bad" not in caplog.text


def test_provider_failure_is_never_presented_as_an_answer(post):
    post.queue.append(response(429, {"error": {"code": "rate_limit_exceeded"}}))
    info = {"mode": "hybrid"}
    res = answer("How are hostel rooms given?", PS, groq_chat, info)
    assert res.status == "fallback" and res.answer is None and res.citations == []
    assert "rate limit" in res.message and len(res.sources) == 2 and info["llm_called"] is True


def test_gated_question_does_not_call_the_model():
    info = {"mode": "hybrid"}
    res = answer("q", [], reply("x", []), info)
    assert res.status == "insufficient_evidence" and info["llm_called"] is False
