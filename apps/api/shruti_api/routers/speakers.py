import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_core.models import Speaker, Transcript, TranscriptSegment

router = APIRouter(prefix="/api", tags=["speakers"])

Db = Annotated[Session, Depends(get_db)]


class RenameBody(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v


class MergeBody(BaseModel):
    into_speaker_id: uuid.UUID


class SegmentEditBody(BaseModel):
    text: str


def speaker_public(s: Speaker, segment_count: int = 0) -> dict:
    return {
        "id": str(s.id),
        "display_name": s.display_name,
        "source_label": s.source_label,
        "segment_count": segment_count,
    }


@router.get("/meetings/{meeting_id}/speakers")
def list_speakers(meeting_id: uuid.UUID, db: Db) -> list[dict]:
    active = select(Transcript.id).where(Transcript.meeting_id == meeting_id, Transcript.is_active)
    counts = dict(
        db.execute(
            select(TranscriptSegment.speaker_id, func.count())
            .where(TranscriptSegment.transcript_id.in_(active))
            .group_by(TranscriptSegment.speaker_id)
        ).all()
    )
    # source_label is chronological (diarizer numbers by first appearance) AND unique,
    # unlike created_at which is identical for speakers inserted in one transaction.
    speakers = db.scalars(
        select(Speaker).where(Speaker.meeting_id == meeting_id).order_by(Speaker.source_label)
    ).all()
    return [speaker_public(s, counts.get(s.id, 0)) for s in speakers]


@router.patch("/speakers/{speaker_id}")
def rename_speaker(speaker_id: uuid.UUID, body: RenameBody, db: Db) -> dict:
    speaker = db.get(Speaker, speaker_id)
    if speaker is None:
        raise HTTPException(status_code=404, detail="speaker not found")
    speaker.display_name = body.display_name
    db.commit()
    return speaker_public(speaker)


@router.post("/speakers/{speaker_id}/merge")
def merge_speaker(speaker_id: uuid.UUID, body: MergeBody, db: Db) -> dict:
    """Fold this speaker into another: repoint every segment, delete this row."""
    source = db.get(Speaker, speaker_id)
    target = db.get(Speaker, body.into_speaker_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="speaker not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="cannot merge a speaker into itself")
    if source.meeting_id != target.meeting_id:
        raise HTTPException(status_code=400, detail="speakers belong to different meetings")

    db.execute(
        update(TranscriptSegment)
        .where(TranscriptSegment.speaker_id == source.id)
        .values(speaker_id=target.id)
    )
    db.delete(source)
    db.commit()
    return speaker_public(target)


@router.patch("/segments/{segment_id}")
def edit_segment(segment_id: uuid.UUID, body: SegmentEditBody, db: Db) -> dict:
    segment = db.get(TranscriptSegment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="segment not found")
    segment.text = body.text.strip()
    segment.words = None  # word timings no longer match the edited text
    db.commit()
    return {"id": str(segment.id), "idx": segment.idx, "text": segment.text}
