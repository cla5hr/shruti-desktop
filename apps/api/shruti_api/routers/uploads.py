from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_api.serializers import meeting_detail
from shruti_core import jobs
from shruti_core.models import Meeting, Recording
from shruti_core.storage import get_storage

router = APIRouter(prefix="/api", tags=["uploads"])

ALLOWED_EXTS = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac", ".mkv", ".wma"}

Db = Annotated[Session, Depends(get_db)]


@router.post("/uploads", status_code=201)
def upload_recording(
    db: Db,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
) -> dict:
    """Manual ingestion path: any meeting recording -> full pipeline."""
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type {ext!r}; allowed: {sorted(ALLOWED_EXTS)}",
        )

    meeting = Meeting(source="upload", title=title.strip() or Path(filename).stem)
    db.add(meeting)
    db.flush()

    key = f"{meeting.id}/original{ext}"
    storage = get_storage()
    written = storage.save(key, file.file)
    if written == 0:
        storage.delete(key)  # don't leave a zero-byte orphan on disk
        db.rollback()
        raise HTTPException(status_code=400, detail="empty file")

    rec = Recording(
        meeting_id=meeting.id,
        original_filename=filename,
        storage_key_original=key,
        size_bytes=written,
    )
    db.add(rec)
    db.commit()

    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
        dedupe_key=f"extract:{rec.id}",
    )
    return meeting_detail(db, meeting)
