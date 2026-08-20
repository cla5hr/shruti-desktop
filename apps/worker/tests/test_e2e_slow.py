"""Real-model end-to-end: upload the TTS fixture, run the whole chain with
faster-whisper tiny on CPU, and check the transcript is plausible.

Run with: uv run pytest -m slow
First run downloads the tiny model (~75MB) to the HF cache.
"""

import pytest
from fastapi.testclient import TestClient

from shruti_api.main import app
from shruti_worker.main import run_until_idle


@pytest.mark.slow
def test_upload_to_transcript_with_whisper_tiny(db, tmp_storage, fixtures_dir):
    client = TestClient(app)
    sample = fixtures_dir / "sample-meeting.mp3"
    with sample.open("rb") as f:
        resp = client.post(
            "/api/uploads",
            files={"file": ("sample-meeting.mp3", f, "audio/mpeg")},
            data={"title": "Launch readiness"},
        )
    assert resp.status_code == 201
    meeting_id = resp.json()["id"]

    processed = run_until_idle(["io", "gpu"], max_jobs=10)
    assert processed == 4  # extract, waveform, asr, summarize (stub LLM)

    detail = client.get(f"/api/meetings/{meeting_id}").json()
    assert detail["status"] == "ready", detail.get("error")
    assert detail["duration_s"] == pytest.approx(69, abs=5)

    transcript = client.get(f"/api/meetings/{meeting_id}/transcript").json()
    assert transcript["language"] == "en"
    assert len(transcript["segments"]) >= 3
    full_text = " ".join(s["text"] for s in transcript["segments"]).lower()
    assert "meeting" in full_text  # whisper-tiny reliably gets this word from the TTS audio
    # timestamps are ordered and sane
    starts = [s["start_ms"] for s in transcript["segments"]]
    assert starts == sorted(starts)
    assert transcript["segments"][-1]["end_ms"] <= 75_000
