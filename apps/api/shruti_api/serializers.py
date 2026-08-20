"""Row -> JSON shapes shared by routers."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from shruti_core.models import Job, Meeting, Recording, Transcript


def iso_utc(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC marker. The DB stores naive UTC (SQLite
    CURRENT_TIMESTAMP); serializing it without a 'Z' made `new Date(...)` in the
    browser parse it as LOCAL time, so every meeting displayed in UTC (5½ hours
    off in IST)."""
    if dt is None:
        return None
    iso = dt.isoformat()
    return iso if dt.tzinfo is not None else iso + "Z"


def job_public(job: Job) -> dict:
    return {
        "id": str(job.id),
        "type": job.type,
        "queue": job.queue,
        "status": job.status,
        "attempts": job.attempts,
        "progress": job.progress,
        "error": job.error,
        "meeting_id": str(job.meeting_id) if job.meeting_id else None,
        "created_at": iso_utc(job.created_at),
        "finished_at": iso_utc(job.finished_at),
    }


def meeting_summary(m: Meeting) -> dict:
    return {
        "id": str(m.id),
        "title": m.title,
        "source": m.source,
        "status": m.status,
        "duration_s": m.duration_s,
        "created_at": iso_utc(m.created_at),
    }


def meeting_detail(session: Session, m: Meeting) -> dict:
    rec = session.scalars(
        select(Recording).where(Recording.meeting_id == m.id).order_by(Recording.created_at)
    ).first()
    active = session.scalars(
        select(Transcript).where(Transcript.meeting_id == m.id, Transcript.is_active)
    ).first()
    out = meeting_summary(m)
    out["recording"] = (
        {
            "id": str(rec.id),
            "original_filename": rec.original_filename,
            "duration_s": rec.duration_s,
            "size_bytes": rec.size_bytes,
            "has_playback": bool(rec.storage_key_playback),
            "has_peaks": bool(rec.storage_key_peaks),
        }
        if rec
        else None
    )
    out["active_transcript_id"] = str(active.id) if active else None
    if m.status == "failed":
        failed = session.scalars(
            select(Job)
            .where(Job.meeting_id == m.id, Job.status == "failed")
            .order_by(Job.finished_at.desc())
        ).first()
        out["error"] = (failed.error or "")[:500] if failed else None
    return out
