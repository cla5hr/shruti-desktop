from fastapi.testclient import TestClient

from shruti_api.main import app


def test_healthz_responds(db):
    # db fixture ensures postgres is up (skips otherwise)
    client = TestClient(app)
    resp = client.get("/api/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")


def test_unknown_job_is_404(db):
    client = TestClient(app)
    resp = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
