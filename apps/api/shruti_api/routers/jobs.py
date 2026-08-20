import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_api.serializers import job_public
from shruti_core import jobs as jobs_queue
from shruti_core.models import Job

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
