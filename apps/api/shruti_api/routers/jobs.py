import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_api.serializers import job_public
from shruti_core import jobs as jobs_queue
from shruti_core.models import Job, Meeting, Transcript

router = APIRouter(prefix="/api", tags=["jobs"])

Db = Annotated[Session, Depends(get_db)]


@router.post("/jobs/noop", status_code=201)
def enqueue_noop(db: Db) -> dict:
    job = jobs_queue.enqueue(db, "noop", queue="io", payload={"hello": "shruti"})
    assert job is not None  # no dedupe key -> always inserts
    return job_public(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, db: Db) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_public(job)


@router.get("/jobs")
def list_jobs(db: Db, meeting_id: uuid.UUID | None = None) -> list[dict]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(100)
    if meeting_id is not None:
        stmt = stmt.where(Job.meeting_id == meeting_id)
    return [job_public(j) for j in db.scalars(stmt).all()]


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, db: Db) -> dict:
    """Stop a queued or running job. Queued jobs die immediately; running ones stop
    at their next progress checkpoint (the worker checks between segments/steps)."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in ("queued", "running"):
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
        job.error = None
        db.commit()
        # nothing else in flight for this meeting? settle its status now (the worker
        # only restores it for jobs it was actively running)
        if job.meeting_id is not None:
            others = db.scalars(
                select(Job).where(
                    Job.meeting_id == job.meeting_id, Job.status.in_(("queued", "running"))
                )
            ).first()
            meeting = db.get(Meeting, job.meeting_id)
            if others is None and meeting is not None and meeting.status == "processing":
                has_transcript = db.scalars(
                    select(Transcript.id).where(
                        Transcript.meeting_id == meeting.id, Transcript.is_active
                    )
                ).first()
                meeting.status = "ready" if has_transcript else "failed"
                db.commit()
    return job_public(job)
