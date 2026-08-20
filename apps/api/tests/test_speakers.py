import io

from fastapi.testclient import TestClient

from shruti_api.main import app
from shruti_core.models import Meeting, Recording, Speaker, Transcript, TranscriptSegment
from shruti_core.storage import get_storage


def seed_meeting(db):
    """Meeting with active transcript, 3 segments, 2 speakers (0&2 -> A, 1 -> B)."""
    meeting = Meeting(source="upload", title="Launch Sync", status="ready", duration_s=69)
    db.add(meeting)
    db.flush()
    sp_a = Speaker(meeting_id=meeting.id, display_name="SPEAKER_00", source_label="SPEAKER_00")
    sp_b = Speaker(meeting_id=meeting.id, display_name="SPEAKER_01", source_label="SPEAKER_01")
    db.add_all([sp_a, sp_b])
    transcript = Transcript(
        meeting_id=meeting.id, kind="whisper_raw", engine="fake", model="tiny", is_active=True
    )
    db.add(transcript)
    db.flush()
    texts = [
        "Good morning everyone.",
        "The propulsion test was successful.",
        "We need one more week of Bench testing before bench sign off.",
    ]
    speaker_of = [sp_a, sp_b, sp_a]
    for i, text in enumerate(texts):
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                idx=i,
                start_ms=i * 5000,
                end_ms=i * 5000 + 4000,
                text=text,
                speaker_id=speaker_of[i].id,
                speaker_label=speaker_of[i].source_label,
            )
        )
    db.commit()
    return meeting, sp_a, sp_b


def test_list_rename_and_transcript_names(db):
    meeting, sp_a, sp_b = seed_meeting(db)
    client = TestClient(app)

    listed = client.get(f"/api/meetings/{meeting.id}/speakers").json()
    assert [(s["display_name"], s["segment_count"]) for s in listed] == [
        ("SPEAKER_00", 2),
        ("SPEAKER_01", 1),
    ]

    resp = client.patch(f"/api/speakers/{sp_a.id}", json={"display_name": "Priya"})
    assert resp.status_code == 200

    transcript = client.get(f"/api/meetings/{meeting.id}/transcript").json()
    assert transcript["segments"][0]["speaker"] == "Priya"
    assert transcript["segments"][1]["speaker"] == "SPEAKER_01"

    assert client.patch(f"/api/speakers/{sp_b.id}", json={"display_name": "  "}).status_code == 422


def test_merge_repoints_segments(db):
    meeting, sp_a, sp_b = seed_meeting(db)
    client = TestClient(app)

    resp = client.post(f"/api/speakers/{sp_b.id}/merge", json={"into_speaker_id": str(sp_a.id)})
    assert resp.status_code == 200

    listed = client.get(f"/api/meetings/{meeting.id}/speakers").json()
    assert len(listed) == 1
    assert listed[0]["segment_count"] == 3

    self_merge = client.post(
        f"/api/speakers/{sp_a.id}/merge", json={"into_speaker_id": str(sp_a.id)}
    )
    assert self_merge.status_code == 400


def test_edit_segment_text(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)
    seg = client.get(f"/api/meetings/{meeting.id}/transcript").json()["segments"][0]
    resp = client.patch(f"/api/segments/{seg['id']}", json={"text": "Good morning, team."})
    assert resp.status_code == 200
    assert (
        client.get(f"/api/meetings/{meeting.id}/transcript").json()["segments"][0]["text"]
        == "Good morning, team."
    )


def test_replace_across_transcript(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)

    resp = client.post(
        f"/api/meetings/{meeting.id}/replace",
        json={"find": "bench", "replace": "vibration"},
    ).json()
    assert resp == {"replacements": 2, "segments_changed": 1}  # case-insensitive

    caseful = client.post(
        f"/api/meetings/{meeting.id}/replace",
        json={"find": "Propulsion", "replace": "engine", "match_case": True},
    ).json()
    assert caseful["replacements"] == 0  # text says "propulsion"


def test_export_markdown(db):
    meeting, sp_a, _ = seed_meeting(db)
    client = TestClient(app)
    client.patch(f"/api/speakers/{sp_a.id}", json={"display_name": "Priya"})

    resp = client.get(f"/api/meetings/{meeting.id}/export.md")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.text
    assert body.startswith("# Launch Sync")
    assert "**Speakers:** Priya, SPEAKER_01" in body
    assert "**[0:00] Priya:** Good morning everyone." in body


def test_retranscribe_enqueues_new_asr(db, tmp_storage):
    meeting, *_ = seed_meeting(db)
    storage = get_storage()
    storage.save(f"{meeting.id}/audio.wav", io.BytesIO(b"riff"))
    rec = Recording(
        meeting_id=meeting.id,
        storage_key_original=f"{meeting.id}/original.mp3",
        storage_key_audio_wav=f"{meeting.id}/audio.wav",
    )
    db.add(rec)
    db.commit()

    client = TestClient(app)
    resp = client.post(f"/api/meetings/{meeting.id}/retranscribe")
    assert resp.status_code == 202
    assert resp.json()["type"] == "asr"

    detail = client.get(f"/api/meetings/{meeting.id}").json()
    assert detail["status"] == "processing"

    # a second explicit retranscribe is allowed (no dedupe on manual runs)
    assert client.post(f"/api/meetings/{meeting.id}/retranscribe").status_code == 202
