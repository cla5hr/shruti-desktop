"""Minutes step: prompt building, budget chunking, map-reduce, versioning."""

import uuid

import pytest
from sqlalchemy import select

from shruti_core.models import Meeting, Summary, Transcript, TranscriptSegment
from shruti_core.settings import get_settings
from shruti_worker.pipeline import summarize as sz


def make_segment(idx: int, text: str, speaker_label: str | None = None) -> TranscriptSegment:
    return TranscriptSegment(
        transcript_id=uuid.uuid4(),
        idx=idx,
        start_ms=idx * 5000,
        end_ms=idx * 5000 + 4000,
        text=text,
        speaker_label=speaker_label,
    )


def test_transcript_lines_format():
    segs = [make_segment(0, "Hello all.", "SPEAKER_00"), make_segment(1, "Hi.", None)]
    names = {}
    out = sz.transcript_lines(segs, names)
    assert out.splitlines() == ["[0:00] SPEAKER_00: Hello all.", "[0:05] Speaker: Hi."]


def test_split_for_budget_preserves_all_lines():
    lines = [f"line {i} " + "x" * 50 for i in range(40)]
    chunks = sz.split_for_budget(lines, budget_tokens=100)
    assert len(chunks) > 1
    assert "\n".join(chunks).count("line ") == 40  # nothing lost
    for chunk in chunks:
        assert sz.estimate_tokens(chunk) <= 100 + 60  # one-line tolerance


def test_generate_minutes_single_call(monkeypatch):
    calls = []
    monkeypatch.setattr(sz, "chat", lambda messages, **kw: calls.append(messages) or "MINUTES")
    monkeypatch.setenv("LLM_MAX_CTX", "16384")
    get_settings.cache_clear()
    out = sz.generate_minutes("standard", "Sync", "[0:00] A: short text", lambda p: None)
    assert out == "MINUTES"
    assert len(calls) == 1
    assert "short text" in calls[0][1]["content"]
    get_settings.cache_clear()


def test_generate_minutes_map_reduce_on_long_transcript(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sz, "chat", lambda messages, **kw: calls.append(messages) or f"NOTES-{len(calls)}"
    )
    monkeypatch.setenv("LLM_MAX_CTX", "300")  # tiny ctx forces chunking
    get_settings.cache_clear()
    text = "\n".join(f"[0:{i:02d}] A: point number {i} " + "detail " * 20 for i in range(30))
    out = sz.generate_minutes("standard", "Long Sync", text, lambda p: None)
    assert len(calls) >= 3  # at least 2 map calls + 1 reduce
    merge_prompt = calls[-1][1]["content"]
    assert "NOTES-1" in merge_prompt  # partials fed into the reduce step
    assert out == f"NOTES-{len(calls)}"
    get_settings.cache_clear()


def test_low_content_transcript_skips_llm(db, tmp_storage):
    """An empty/near-silent recording must NOT be sent to the LLM (it hallucinates
    minutes with fake [m:ss] citations). It gets a fixed 'unavailable' notice."""
    from shruti_core import jobs as q
    from shruti_worker.main import run_until_idle

    meeting = Meeting(source="upload", title="silent", status="ready")
    db.add(meeting)
    db.flush()
    transcript = Transcript(
        meeting_id=meeting.id, kind="whisper_raw", engine="fake", model="t", is_active=True
    )
    db.add(transcript)
    db.flush()
    seg = make_segment(0, "uh hello")  # 2 words, below the threshold
    seg.transcript_id = transcript.id
    db.add(seg)
    db.commit()

    q.enqueue(
        db,
        "summarize",
        queue="io",
        meeting_id=meeting.id,
        payload={"transcript_id": str(transcript.id), "template": "standard"},
    )
    run_until_idle(["io"])

    summary = db.scalars(select(Summary).where(Summary.meeting_id == meeting.id)).one()
    assert "Minutes unavailable" in summary.content_md
    assert "[0:" not in summary.content_md  # no invented timestamps


def test_persist_summary_versioning(db):
    meeting = Meeting(source="upload", title="v")
    db.add(meeting)
    db.flush()
    transcript = Transcript(
        meeting_id=meeting.id, kind="whisper_raw", engine="fake", model="t", is_active=True
    )
    db.add(transcript)
    db.flush()

    s1 = sz.persist_summary(
        db, meeting.id, transcript.id, "v1", template_key="standard", model="stub"
    )
    db.commit()
    s2 = sz.persist_summary(db, meeting.id, transcript.id, "v2", template_key="brief", model="stub")
    db.commit()

    db.refresh(s1)
    db.refresh(s2)
    assert s1.is_active is False
    assert s2.is_active is True
    actives = db.scalars(
        select(Summary).where(Summary.meeting_id == meeting.id, Summary.is_active)
    ).all()
    assert len(actives) == 1


def test_unknown_template_fails(db):
    from shruti_core import jobs as q

    meeting = Meeting(source="upload", title="x")
    db.add(meeting)
    db.flush()
    transcript = Transcript(
        meeting_id=meeting.id, kind="whisper_raw", engine="fake", model="t", is_active=True
    )
    db.add(transcript)
    db.commit()

    job = q.enqueue(
        db,
        "summarize",
        queue="io",
        meeting_id=meeting.id,
        payload={"transcript_id": str(transcript.id), "template": "nope"},
        max_attempts=1,
    )
    from shruti_worker.main import run_until_idle

    run_until_idle(["io"])
    db.refresh(job)
    assert job.status == "failed"
    assert "unknown summary template" in job.error


@pytest.fixture()
def _noop():
    return None


def test_clean_minutes_strips_model_artifacts():
    from shruti_worker.pipeline.summarize import clean_minutes

    raw = "\n".join(
        [
            "## Summary — 3-5 sentences on what the meeting covered and concluded.",
            "The team reviewed the launch readiness.",
            "## Key Points",
            "- - [ ] double bulleted item",
            "- [0:12] SPEAKER_00 confirmed the schedule.",
            "- [0:30] SPEAKER_3: We should outsource tasks.",
            "Produce minutes with exactly these sections:",
            "## Action Items",
            "- **SPEAKER_00** — send the report",
        ]
    )
    cleaned = clean_minutes(raw, {"SPEAKER_00"})
    assert "## Summary" in cleaned
    assert "3-5 sentences" not in cleaned  # echoed heading description stripped
    assert "Produce minutes" not in cleaned  # echoed instruction line dropped
    assert "- [ ] double bulleted item" in cleaned  # double bullet fixed
    assert "SPEAKER_3" not in cleaned  # invented speaker bullet dropped
    assert "SPEAKER_00 confirmed the schedule" in cleaned  # real speaker kept
    assert "send the report" in cleaned
