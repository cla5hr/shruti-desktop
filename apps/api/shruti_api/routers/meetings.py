import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_api.serializers import job_public, meeting_detail, meeting_summary
from shruti_core import jobs
from shruti_core.models import (
    ChatMessage,
    ChatThread,
    Job,
    Meeting,
    Recording,
    Speaker,
    Summary,
    Transcript,
    TranscriptSegment,
)
from shruti_core.storage import get_storage

router = APIRouter(prefix="/api", tags=["meetings"])

Db = Annotated[Session, Depends(get_db)]


def _clock(ms: int) -> str:
    total_s = ms // 1000
    h, m, s = total_s // 3600, (total_s % 3600) // 60, total_s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _get_meeting(db: Session, meeting_id: uuid.UUID) -> Meeting:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return meeting


@router.get("/meetings")
def list_meetings(db: Db) -> list[dict]:
    rows = db.scalars(select(Meeting).order_by(Meeting.created_at.desc()).limit(200)).all()
    return [meeting_summary(m) for m in rows]


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: uuid.UUID, db: Db) -> dict:
    return meeting_detail(db, _get_meeting(db, meeting_id))


class RenameMeetingBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)


@router.patch("/meetings/{meeting_id}")
def rename_meeting(meeting_id: uuid.UUID, body: RenameMeetingBody, db: Db) -> dict:
    meeting = _get_meeting(db, meeting_id)
    meeting.title = body.title.strip() or meeting.title
    db.commit()
    return meeting_detail(db, meeting)


@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: uuid.UUID, db: Db) -> None:
    """Delete a meeting and everything under it: transcripts, speakers, summaries,
    chat, jobs, bot session, and all stored audio/waveform files."""
    meeting = _get_meeting(db, meeting_id)

    # storage: remove every file under this meeting's prefix
    storage = get_storage()
    for key in list(storage.iter_keys(str(meeting_id))):
        storage.delete(key)

    # DB: delete children before parents (no ON DELETE CASCADE in the schema)
    transcript_ids = db.scalars(
        select(Transcript.id).where(Transcript.meeting_id == meeting_id)
    ).all()
    thread_ids = db.scalars(select(ChatThread.id).where(ChatThread.meeting_id == meeting_id)).all()
    if thread_ids:
        db.execute(delete(ChatMessage).where(ChatMessage.thread_id.in_(thread_ids)))
    db.execute(delete(ChatThread).where(ChatThread.meeting_id == meeting_id))
    db.execute(delete(Summary).where(Summary.meeting_id == meeting_id))
    if transcript_ids:
        db.execute(
            delete(TranscriptSegment).where(TranscriptSegment.transcript_id.in_(transcript_ids))
        )
    db.execute(delete(Transcript).where(Transcript.meeting_id == meeting_id))
    db.execute(delete(Speaker).where(Speaker.meeting_id == meeting_id))
    db.execute(delete(Recording).where(Recording.meeting_id == meeting_id))
    db.execute(delete(Job).where(Job.meeting_id == meeting_id))
    db.delete(meeting)
    db.commit()


def _active_transcript(db: Session, meeting: Meeting) -> Transcript:
    transcript = db.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.is_active)
    ).first()
    if transcript is None:
        raise HTTPException(status_code=404, detail="no transcript yet")
    return transcript


def _segments(db: Session, transcript: Transcript) -> list[TranscriptSegment]:
    return list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.idx)
        )
    )


def _speaker_names(db: Session, meeting_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return {
        s.id: s.display_name
        for s in db.scalars(select(Speaker).where(Speaker.meeting_id == meeting_id))
    }


def _job_in_flight(db: Session, meeting_id: uuid.UUID, job_type: str) -> Job | None:
    """An explicit re-run has no dedupe key, so rapid re-clicks would stack multi-minute
    jobs (a frozen-looking UI once queued 21 diarize runs). Return the in-flight job
    instead of enqueueing another."""
    return db.scalars(
        select(Job).where(
            Job.meeting_id == meeting_id,
            Job.type == job_type,
            Job.status.in_(("queued", "running")),
        )
    ).first()


@router.get("/meetings/{meeting_id}/transcript")
def get_transcript(meeting_id: uuid.UUID, db: Db) -> dict:
    meeting = _get_meeting(db, meeting_id)
    transcript = _active_transcript(db, meeting)
    names = _speaker_names(db, meeting.id)
    return {
        "id": str(transcript.id),
        "kind": transcript.kind,
        "engine": transcript.engine,
        "model": transcript.model,
        "language": transcript.language,
        "segments": [
            {
                "id": str(s.id),
                "idx": s.idx,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "speaker_id": str(s.speaker_id) if s.speaker_id else None,
                "speaker": names.get(s.speaker_id, s.speaker_label),
                "text": s.text,
            }
            for s in _segments(db, transcript)
        ],
    }


class ReplaceBody(BaseModel):
    find: str = Field(min_length=1)
    replace: str
    match_case: bool = False


@router.post("/meetings/{meeting_id}/replace")
def replace_in_transcript(meeting_id: uuid.UUID, body: ReplaceBody, db: Db) -> dict:
    """Search & replace across the active transcript — fix names/jargon before summarizing."""
    meeting = _get_meeting(db, meeting_id)
    transcript = _active_transcript(db, meeting)
    pattern = re.compile(re.escape(body.find), 0 if body.match_case else re.IGNORECASE)

    replacements = 0
    segments_changed = 0
    for segment in _segments(db, transcript):
        new_text, n = pattern.subn(body.replace, segment.text)
        if n:
            segment.text = new_text
            segment.words = None
            replacements += n
            segments_changed += 1
    db.commit()
    return {"replacements": replacements, "segments_changed": segments_changed}


@router.post("/meetings/{meeting_id}/retranscribe", status_code=202)
def retranscribe(meeting_id: uuid.UUID, db: Db) -> dict:
    """Re-run ASR (and diarization) as a new transcript version; old versions kept."""
    meeting = _get_meeting(db, meeting_id)
    rec = db.scalars(select(Recording).where(Recording.meeting_id == meeting.id)).first()
    if rec is None or not rec.storage_key_audio_wav:
        raise HTTPException(status_code=409, detail="no processed recording to retranscribe")
    if existing := _job_in_flight(db, meeting.id, "asr"):
        return job_public(existing)
    meeting.status = "processing"
    db.commit()
    job = jobs.enqueue(
        db,
        "asr",
        queue="gpu",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
        # no dedupe: an explicit retranscribe is always a fresh run
    )
    assert job is not None
    return job_public(job)


@router.post("/meetings/{meeting_id}/rediarize", status_code=202)
def rediarize(meeting_id: uuid.UUID, db: Db) -> dict:
    """Re-run speaker separation on the existing transcript (no re-transcription) —
    used after changing the people-count setting or when the auto-detect got it wrong."""
    meeting = _get_meeting(db, meeting_id)
    rec = db.scalars(select(Recording).where(Recording.meeting_id == meeting.id)).first()
    if rec is None or not rec.storage_key_audio_wav:
        raise HTTPException(status_code=409, detail="no processed audio to re-analyse")
    transcript = _active_transcript(db, meeting)
    if existing := _job_in_flight(db, meeting.id, "diarize"):
        return job_public(existing)
    meeting.status = "processing"
    db.commit()
    job = jobs.enqueue(
        db,
        "diarize",
        queue="gpu",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id), "transcript_id": str(transcript.id)},
        # no dedupe: an explicit re-run is always a fresh attempt
    )
    assert job is not None
    return job_public(job)


@router.get("/meetings/{meeting_id}/export.md")
def export_markdown(meeting_id: uuid.UUID, db: Db) -> PlainTextResponse:
    meeting = _get_meeting(db, meeting_id)
    transcript = _active_transcript(db, meeting)
    names = _speaker_names(db, meeting.id)
    segments = _segments(db, transcript)

    lines = [f"# {meeting.title or 'Untitled meeting'}", ""]
    if meeting.created_at:
        lines.append(f"- **Date:** {meeting.created_at.strftime('%d %b %Y, %H:%M')}")
    if meeting.duration_s:
        lines.append(f"- **Duration:** {_clock(meeting.duration_s * 1000)}")
    speaker_list = sorted(
        {
            names.get(s.speaker_id, s.speaker_label)
            for s in segments
            if s.speaker_id or s.speaker_label
        }
        - {None}
    )
    if speaker_list:
        lines.append(f"- **Speakers:** {', '.join(speaker_list)}")
    lines += ["", "## Transcript", ""]
    for s in segments:
        who = names.get(s.speaker_id, s.speaker_label)
        prefix = f"**[{_clock(s.start_ms)}] {who}:**" if who else f"**[{_clock(s.start_ms)}]**"
        lines.append(f"{prefix} {s.text}")
        lines.append("")

    filename = re.sub(r"[^\w\- ]", "", meeting.title or "meeting").strip() or "meeting"
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )
