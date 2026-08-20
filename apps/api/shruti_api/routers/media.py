"""Audio playback + waveform peaks. Files are served through the API (never a
public static dir) so per-meeting access control can attach here in Phase 7."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_core.models import Recording
from shruti_core.storage import get_storage

router = APIRouter(prefix="/api", tags=["media"])

Db = Annotated[Session, Depends(get_db)]


def _recording(db: Session, meeting_id: uuid.UUID) -> Recording:
    rec = db.scalars(
        select(Recording).where(Recording.meeting_id == meeting_id).order_by(Recording.created_at)
    ).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="no recording for this meeting")
    return rec


@router.get("/meetings/{meeting_id}/audio")
def get_audio(meeting_id: uuid.UUID, db: Db) -> FileResponse:
    rec = _recording(db, meeting_id)
    if not rec.storage_key_playback:
        raise HTTPException(status_code=404, detail="playback audio not ready yet")
    path = get_storage().path(rec.storage_key_playback)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="playback file missing")
    # FileResponse handles Range requests (206) — required for <audio> seeking
    return FileResponse(path, media_type="audio/mp4", filename="meeting.m4a")


@router.get("/meetings/{meeting_id}/peaks")
def get_peaks(meeting_id: uuid.UUID, db: Db) -> Response:
    rec = _recording(db, meeting_id)
    if not rec.storage_key_peaks:
        raise HTTPException(status_code=404, detail="waveform not ready yet")
    data = get_storage().open(rec.storage_key_peaks).read()
    return Response(content=data, media_type="application/json")
