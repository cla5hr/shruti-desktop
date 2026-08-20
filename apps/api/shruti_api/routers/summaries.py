import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_api.routers.meetings import _job_in_flight
from shruti_api.serializers import job_public
from shruti_core import jobs
from shruti_core.models import Meeting, Summary, Transcript

router = APIRouter(prefix="/api", tags=["summaries"])

Db = Annotated[Session, Depends(get_db)]

TEMPLATE_KEYS = {"standard", "brief"}  # mirrors worker prompts.TEMPLATES


def summary_public(s: Summary) -> dict:
    return {
        "id": str(s.id),
        "template_key": s.template_key,
        "content_md": s.content_md,
        "model": s.model,
        "edited": s.edited,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _active_summary(db: Session, meeting_id: uuid.UUID) -> Summary | None:
    return db.scalars(
        select(Summary).where(Summary.meeting_id == meeting_id, Summary.is_active)
    ).first()


@router.get("/meetings/{meeting_id}/summary")
def get_summary(meeting_id: uuid.UUID, db: Db) -> dict:
    summary = _active_summary(db, meeting_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="no minutes yet")
    return summary_public(summary)


class SummaryEditBody(BaseModel):
    content_md: str


@router.put("/meetings/{meeting_id}/summary")
def edit_summary(meeting_id: uuid.UUID, body: SummaryEditBody, db: Db) -> dict:
    summary = _active_summary(db, meeting_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="no minutes to edit")
    summary.content_md = body.content_md
    summary.edited = True
    db.commit()
    return summary_public(summary)


class SummarizeBody(BaseModel):
    template_key: str = "standard"


@router.post("/meetings/{meeting_id}/summarize", status_code=202)
def request_summary(meeting_id: uuid.UUID, body: SummarizeBody, db: Db) -> dict:
    if body.template_key not in TEMPLATE_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown template {body.template_key!r}")
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    transcript = db.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting_id, Transcript.is_active)
    ).first()
    if transcript is None:
        raise HTTPException(status_code=409, detail="no transcript to summarize yet")
    if existing := _job_in_flight(db, meeting_id, "summarize"):
        return job_public(existing)
    job = jobs.enqueue(
        db,
        "summarize",
        queue="io",
        meeting_id=meeting_id,
        payload={"transcript_id": str(transcript.id), "template": body.template_key},
        # no dedupe: explicit regenerate is always a fresh run
    )
    assert job is not None
    return job_public(job)
