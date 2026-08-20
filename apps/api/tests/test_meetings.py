import io

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_speakers import seed_meeting

from shruti_api.main import app
from shruti_core.models import Meeting, Speaker, TranscriptSegment
from shruti_core.storage import get_storage


def test_delete_meeting_cascades(db, tmp_storage):
    meeting, sp_a, _ = seed_meeting(db)
    mid = meeting.id
    # a stored file under the meeting prefix
    get_storage().save(f"{mid}/audio.wav", io.BytesIO(b"riff"))

    client = TestClient(app)
    resp = client.delete(f"/api/meetings/{mid}")
    assert resp.status_code == 204

    db.expire_all()  # the delete committed in the API's session; re-read from DB
    assert db.get(Meeting, mid) is None
    assert (
        db.execute(
            select(func.count()).select_from(Speaker).where(Speaker.meeting_id == mid)
        ).scalar()
        == 0
    )
    assert db.execute(select(func.count()).select_from(TranscriptSegment)).scalar() == 0
    assert not get_storage().exists(f"{mid}/audio.wav")
    assert client.get(f"/api/meetings/{mid}").status_code == 404


def test_delete_missing_meeting_404(db):
    client = TestClient(app)
    assert client.delete("/api/meetings/00000000-0000-0000-0000-000000000000").status_code == 404


def test_rename_meeting(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)

    resp = client.patch(f"/api/meetings/{meeting.id}", json={"title": "  Launch readiness  "})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Launch readiness"
    assert client.get("/api/meetings").json()[0]["title"] == "Launch readiness"

    # blank titles are rejected by validation
    assert client.patch(f"/api/meetings/{meeting.id}", json={"title": ""}).status_code == 422
