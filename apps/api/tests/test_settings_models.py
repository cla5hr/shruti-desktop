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
