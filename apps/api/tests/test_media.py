import io
import json

from fastapi.testclient import TestClient

from shruti_api.main import app
from shruti_core.models import Meeting, Recording
from shruti_core.storage import get_storage


def _ready_meeting(db):
    meeting = Meeting(source="upload", title="m", status="ready")
    db.add(meeting)
    db.flush()
    storage = get_storage()
    audio = b"\x00\x01" * 4000
    storage.save(f"{meeting.id}/playback.m4a", io.BytesIO(audio))
    peaks = json.dumps({"version": 1, "peaks": [[-0.5, 0.5]] * 100}).encode()
    storage.save(f"{meeting.id}/peaks.json", io.BytesIO(peaks))
    rec = Recording(
        meeting_id=meeting.id,
        storage_key_original=f"{meeting.id}/original.mp3",
        storage_key_playback=f"{meeting.id}/playback.m4a",
        storage_key_peaks=f"{meeting.id}/peaks.json",
    )
    db.add(rec)
    db.commit()
    return meeting, audio


def test_audio_full_and_range_requests(db, tmp_storage):
    meeting, audio = _ready_meeting(db)
    client = TestClient(app)

    full = client.get(f"/api/meetings/{meeting.id}/audio")
    assert full.status_code == 200
    assert full.content == audio
    assert full.headers["accept-ranges"] == "bytes"

    partial = client.get(f"/api/meetings/{meeting.id}/audio", headers={"Range": "bytes=100-199"})
    assert partial.status_code == 206  # <audio> seeking depends on this
    assert partial.content == audio[100:200]
    assert partial.headers["content-range"] == f"bytes 100-199/{len(audio)}"


def test_peaks_endpoint(db, tmp_storage):
    meeting, _ = _ready_meeting(db)
    client = TestClient(app)
    resp = client.get(f"/api/meetings/{meeting.id}/peaks")
    assert resp.status_code == 200
    assert len(resp.json()["peaks"]) == 100


def test_media_404_before_pipeline(db, tmp_storage):
    meeting = Meeting(source="upload", title="empty")
    db.add(meeting)
    db.flush()
    rec = Recording(meeting_id=meeting.id, storage_key_original=f"{meeting.id}/original.mp3")
    db.add(rec)
    db.commit()
    client = TestClient(app)
    assert client.get(f"/api/meetings/{meeting.id}/audio").status_code == 404
    assert client.get(f"/api/meetings/{meeting.id}/peaks").status_code == 404
