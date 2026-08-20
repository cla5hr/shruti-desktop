"""Pipeline step tests — fast: canned fixtures + a fake ASR backend, tiny generated WAVs.

Real ffmpeg is exercised (it's a hard prerequisite); real Whisper models are not
(see test_e2e_slow.py for that).
"""

import json

import pytest
from helpers import make_meeting_with_upload, make_wav_bytes
from sqlalchemy import select

from shruti_core import jobs
from shruti_core.models import Job, Meeting, Transcript, TranscriptSegment
from shruti_core.storage import get_storage
from shruti_worker.main import run_until_idle
from shruti_worker.pipeline.asr import persist_transcript


def test_full_chain_with_fake_asr(db, tmp_storage, fake_asr):
    """upload -> extract_audio -> waveform -> asr(fake) -> meeting ready."""
    meeting, rec = make_meeting_with_upload(db, make_wav_bytes(1.0))
    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
        dedupe_key=f"extract:{rec.id}",
    )

    processed = run_until_idle(["io", "gpu"])
    assert processed == 4  # extract, waveform, asr, summarize (stub LLM)

    db.refresh(meeting)
    db.refresh(rec)
    assert meeting.status == "ready"

    from shruti_core.models import Summary

    summary = db.scalars(select(Summary).where(Summary.meeting_id == meeting.id)).one()
    assert summary.is_active
    assert "## Action Items" in summary.content_md
    assert rec.storage_key_audio_wav and get_storage().exists(rec.storage_key_audio_wav)
    assert rec.storage_key_playback and get_storage().exists(rec.storage_key_playback)
    assert rec.storage_key_peaks and get_storage().exists(rec.storage_key_peaks)
    assert rec.duration_s == pytest.approx(1.0, abs=0.2)

    peaks = json.loads(get_storage().open(rec.storage_key_peaks).read())
    assert len(peaks["peaks"]) > 10
    assert all(-1.0 <= lo <= hi <= 1.0 for lo, hi in peaks["peaks"])

    transcript = db.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.is_active)
    ).one()
    segs = db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.transcript_id == transcript.id)
        .order_by(TranscriptSegment.idx)
    ).all()
    assert len(segs) == len(fake_asr.segments)
    assert segs[0].text.startswith("Good morning")
    assert segs[0].words[0]["w"] == "Good"


def test_retranscribe_versioning(db, fake_asr):
    meeting = Meeting(source="upload", title="v")
    db.add(meeting)
    db.flush()

    t1 = persist_transcript(db, meeting.id, fake_asr, kind="whisper_raw", engine="fake", model="a")
    db.commit()
    t2 = persist_transcript(db, meeting.id, fake_asr, kind="whisper_raw", engine="fake", model="b")
    db.commit()

    db.refresh(t1)
    db.refresh(t2)
    assert t1.is_active is False
    assert t2.is_active is True
    actives = db.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.is_active)
    ).all()
    assert len(actives) == 1


def test_asr_falls_back_to_cpu_when_gpu_fails(db, tmp_storage, fake_asr, monkeypatch):
    """A saved asr_device=cuda that dies at compute time (missing cuBLAS in the exe)
    must degrade to CPU in the same run, not crash-loop the meeting in 'processing'."""
    from shruti_core.settings import get_settings
    from shruti_worker.pipeline.asr import BACKENDS

    class GpuBrokenBackend:
        name = "gpu-broken"

        def transcribe(self, wav_path, settings, progress=None):
            if settings.asr_device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return fake_asr  # CPU path works

    monkeypatch.setitem(BACKENDS, "gpu-broken", GpuBrokenBackend())
    monkeypatch.setenv("ASR_BACKEND", "gpu-broken")
    monkeypatch.setenv("ASR_DEVICE", "cuda")
    # force the pre-flight CUDA gate open so the exception-fallback path is exercised
    monkeypatch.setattr("shruti_core.cuda.cuda_usable", lambda: True)
    get_settings.cache_clear()

    meeting, rec = make_meeting_with_upload(db, make_wav_bytes(1.0))
    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
    )
    run_until_idle(["io", "gpu"])
    get_settings.cache_clear()

    db.refresh(meeting)
    assert meeting.status == "ready"  # transcribed on CPU despite the GPU failure
    segs = db.scalars(select(TranscriptSegment)).all()
    assert len(segs) == len(fake_asr.segments)


def test_corrupt_upload_fails_meeting(db, tmp_storage):
    meeting, rec = make_meeting_with_upload(db, b"this is not audio at all", ext=".mp3")
    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
        max_attempts=1,
    )
    run_until_idle(["io", "gpu"])

    db.refresh(meeting)
    assert meeting.status == "failed"
    job = db.scalars(select(Job).where(Job.type == "extract_audio")).one()
    assert job.status == "failed"
    assert "ffmpeg" in (job.error or "").lower()


def test_invalid_media_fails_fast(db, tmp_storage):
    """A file ffmpeg can never read must fail on the FIRST attempt (and take the
    meeting to 'failed'), not spend ~15 minutes in retry backoff showing QUEUED."""
    from helpers import make_meeting_with_upload
    from sqlalchemy import select

    from shruti_core import jobs as q
    from shruti_core.models import Job
    from shruti_worker.main import run_until_idle

    meeting, rec = make_meeting_with_upload(db, b"this is not audio at all", ext=".mp3")
    q.enqueue(
        db, "extract_audio", queue="io", meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
    )
    run_until_idle(["io"])

    job = db.scalars(
        select(Job).where(Job.meeting_id == meeting.id, Job.type == "extract_audio")
    ).one()
    assert job.status == "failed"
    assert job.attempts == 1
    db.refresh(meeting)
    assert meeting.status == "failed"
