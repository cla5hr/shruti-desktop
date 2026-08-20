"""Settings model listing: fills the hosted-provider model dropdown."""

from fastapi.testclient import TestClient

from shruti_api.main import app


def test_list_models_returns_names(monkeypatch):
    import shruti_core.llm as llm

    monkeypatch.setattr(llm, "list_models", lambda url, key="": (["a:1b", "b:2b"], ""))
    client = TestClient(app)
    resp = client.post(
        "/api/settings/list-models",
        json={"llm_base_url": "http://192.168.10.81:11434/v1", "llm_api_key": ""},
    )
    assert resp.status_code == 200
    assert resp.json() == {"models": ["a:1b", "b:2b"], "detail": ""}


def test_list_models_surfaces_reason_when_empty(monkeypatch):
    import shruti_core.llm as llm

    monkeypatch.setattr(llm, "list_models", lambda url, key="": ([], "The API key was rejected"))
    client = TestClient(app)
    resp = client.post(
        "/api/settings/list-models",
        json={"llm_base_url": "https://api.groq.com/openai/v1", "llm_api_key": "bad"},
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == []
    assert "rejected" in resp.json()["detail"]


def test_list_models_rejects_a_url_without_scheme():
    client = TestClient(app)
    resp = client.post(
        "/api/settings/list-models", json={"llm_base_url": "192.168.10.81:11434", "llm_api_key": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == []
    assert "base URL" in resp.json()["detail"]


def test_list_models_drops_embedding_models(monkeypatch):
    """Embedding models can't chat — they must never reach the picker."""
    import httpx

    import shruti_core.llm as llm

    def fake_get(url, **kw):
        payload = {"data": [{"id": "qwen3.8:27b"}, {"id": "nomic-embed-text:latest"}]}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(llm.httpx, "get", fake_get)
    models, detail = llm.list_models("http://192.168.10.81:11434/v1")
    assert models == ["qwen3.8:27b"]
    assert detail == ""


def test_local_ollama_list_ignores_a_remote_saved_url(monkeypatch):
    """"Ollama on this computer" must describe THIS machine, even when the saved
    provider is a remote Ollama server (that used to leak the company server's
    models into the local picker)."""
    from shruti_api.routers import app_settings as mod

    asked: list[str] = []

    def fake_models(base_url, timeout=0.5, use_cache=True):
        asked.append(base_url)
        return ["qwen2.5:3b"]

    monkeypatch.setattr(mod, "_ollama_models", fake_models)
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_BASE_URL", "http://192.168.10.81:11434/v1")  # remote
    monkeypatch.setenv("LLM_MODEL", "qwen3.8:27b")
    from shruti_core.settings import get_settings

    get_settings.cache_clear()
    try:
        resp = TestClient(app).get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["meta"]["ollama"]["models"] == ["qwen2.5:3b"]
        assert asked == [mod.LOCAL_OLLAMA]
    finally:
        get_settings.cache_clear()
