from fastapi.testclient import TestClient
from sqlalchemy import select

from shruti_api.main import app
from shruti_core.models import Job


def test_upload_creates_meeting_and_enqueues_extract(db, tmp_storage):
    client = TestClient(app)
    resp = client.post(
        "/api/uploads",
        files={"file": ("standup.mp3", b"fake-mp3-bytes", "audio/mpeg")},
        data={"title": "Daily Standup"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Daily Standup"
    assert body["status"] == "pending"
    assert body["source"] == "upload"
    assert body["recording"]["original_filename"] == "standup.mp3"
    assert body["recording"]["size_bytes"] == len(b"fake-mp3-bytes")

    job = db.scalars(select(Job).where(Job.type == "extract_audio")).one()
    assert str(job.meeting_id) == body["id"]
    assert job.status == "queued"


def test_upload_title_defaults_to_filename(db, tmp_storage):
    client = TestClient(app)
    resp = client.post("/api/uploads", files={"file": ("Weekly Sync.wav", b"x", "audio/wav")})
    assert resp.status_code == 201
    assert resp.json()["title"] == "Weekly Sync"


def test_upload_rejects_unsupported_extension(db, tmp_storage):
    client = TestClient(app)
    resp = client.post("/api/uploads", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert resp.status_code == 400


def test_meeting_list_detail_and_missing_transcript(db, tmp_storage):
    client = TestClient(app)
    created = client.post("/api/uploads", files={"file": ("a.mp3", b"x", "audio/mpeg")}).json()

    listing = client.get("/api/meetings").json()
    assert any(m["id"] == created["id"] for m in listing)

    detail = client.get(f"/api/meetings/{created['id']}").json()
    assert detail["recording"] is not None
    assert detail["active_transcript_id"] is None

    assert client.get(f"/api/meetings/{created['id']}/transcript").status_code == 404
    assert client.get("/api/meetings/00000000-0000-0000-0000-000000000000").status_code == 404
