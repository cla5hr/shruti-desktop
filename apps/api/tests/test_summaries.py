from fastapi.testclient import TestClient
from sqlalchemy import select
from test_speakers import seed_meeting  # same-dir import (pytest prepend mode)

from shruti_api.main import app
from shruti_core.models import Job, Summary


def test_summary_404_before_generation(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)
    assert client.get(f"/api/meetings/{meeting.id}/summary").status_code == 404


def test_summary_get_edit_roundtrip(db):
    meeting, *_ = seed_meeting(db)
    from shruti_core.models import Transcript

    transcript = db.scalars(select(Transcript)).one()
    db.add(
        Summary(
            meeting_id=meeting.id,
            transcript_id=transcript.id,
            content_md="## Summary\n\nOriginal.",
            model="stub",
        )
    )
    db.commit()

    client = TestClient(app)
    got = client.get(f"/api/meetings/{meeting.id}/summary").json()
    assert got["content_md"].startswith("## Summary")
    assert got["edited"] is False

    put = client.put(
        f"/api/meetings/{meeting.id}/summary",
        json={"content_md": "## Summary\n\nHand-edited."},
    ).json()
    assert put["edited"] is True

    again = client.get(f"/api/meetings/{meeting.id}/summary").json()
    assert "Hand-edited" in again["content_md"]


def test_request_summary_enqueues_job(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)

    resp = client.post(f"/api/meetings/{meeting.id}/summarize", json={"template_key": "brief"})
    assert resp.status_code == 202
    job = db.scalars(select(Job).where(Job.type == "summarize")).one()
    assert job.payload["template"] == "brief"

    # explicit regenerate is never deduped
    assert client.post(f"/api/meetings/{meeting.id}/summarize", json={}).status_code == 202

    assert (
        client.post(
            f"/api/meetings/{meeting.id}/summarize", json={"template_key": "bogus"}
        ).status_code
        == 400
    )
