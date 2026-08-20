"""LLM client: stub shape, token heuristic, and transport routing (no network)."""

import pytest

from shruti_core import llm
from shruti_core.settings import get_settings


def test_stub_returns_mom_shaped_markdown(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "stub")
    get_settings.cache_clear()
    out = llm.chat([{"role": "user", "content": "x" * 100}])
    assert "## Summary" in out
    assert "## Action Items" in out
    assert "stub:prompt_chars=100" in out
    get_settings.cache_clear()


def test_estimate_tokens_scales():
    assert llm.estimate_tokens("") == 1
    assert llm.estimate_tokens("a" * 300) == 100
    assert llm.estimate_tokens("a" * 3000) > llm.estimate_tokens("a" * 300)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture()
def capture_post(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if "/api/chat" in url:
            return _FakeResponse({"message": {"content": "ollama-reply"}})
        return _FakeResponse({"choices": [{"message": {"content": "openai-reply"}}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    return calls


def test_ollama_transport_sends_num_ctx(monkeypatch, capture_post):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MAX_CTX", "16384")
    get_settings.cache_clear()

    out = llm.chat([{"role": "user", "content": "hi"}])
    assert out == "ollama-reply"
    call = capture_post[0]
    assert call["url"] == "http://localhost:11434/api/chat"  # native API, not /v1
    assert call["json"]["options"]["num_ctx"] == 16384  # the 4096-truncation defuser
    get_settings.cache_clear()


def test_openai_transport_for_non_ollama(monkeypatch, capture_post):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_BASE_URL", "http://spark:8080/v1")
    get_settings.cache_clear()

    out = llm.chat([{"role": "user", "content": "hi"}], max_tokens=99)
    assert out == "openai-reply"
    call = capture_post[0]
    assert call["url"] == "http://spark:8080/v1/chat/completions"
    assert call["json"]["max_tokens"] == 99
    assert "options" not in call["json"]
    get_settings.cache_clear()
