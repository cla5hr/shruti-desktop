"""SQLAlchemy models — the single source of truth for the schema.

The desktop exe creates the SQLite schema straight from these models
(Base.metadata.create_all) — there is no migration machinery.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, MetaData, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

GUID = Uuid()  # stored as TEXT on SQLite
JSONCol = JSON()

# Deterministic constraint names keep DDL stable across machines
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(GUID, primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(Text, default="user")  # user | admin
    entra_oid: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(Text, default="upload")  # record-here / file upload
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="pending")
    # pending | processing | ready | failed
    organizer_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """A unit of pipeline work. See shruti_core/jobs.py for queue semantics."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "queue", "run_after", "priority", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(Text)  # noop | extract_audio | asr | diarize | ...
    queue: Mapped[str] = mapped_column(Text, default="io")  # io | gpu
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meetings.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONCol, default=dict)
    status: Mapped[str] = mapped_column(Text, default="queued")
    # queued | running | succeeded | failed | cancelled
    priority: Mapped[int] = mapped_column(default=100)  # lower runs first
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONCol, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"))
    original_filename: Mapped[str] = mapped_column(Text, default="")
    storage_key_original: Mapped[str] = mapped_column(Text)
    storage_key_audio_wav: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key_playback: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key_peaks: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transcript(Base):
    """Versioned: retranscribing creates a new row; exactly one is_active per meeting."""

    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"))
    kind: Mapped[str] = mapped_column(Text)  # whisper_raw | final
    engine: Mapped[str] = mapped_column(Text)  # faster_whisper | whisperx
    model: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Summary(Base):
    """Minutes of Meeting. Versioned like transcripts: regenerate creates a new
    active row; user edits mutate content_md in place and set edited=True."""

    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"))
    transcript_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcripts.id"))
    template_key: Mapped[str] = mapped_column(Text, default="standard")
    content_md: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text, default="")
    edited: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Speaker(Base):
    """One diarized voice in a meeting. Rename edits display_name; merge repoints
    segments to the target speaker and deletes this row."""

    __tablename__ = "speakers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"))
    display_name: Mapped[str] = mapped_column(Text)  # starts as source_label, user-editable
    source_label: Mapped[str] = mapped_column(Text)  # e.g. SPEAKER_00 (immutable)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id: Mapped[uuid.UUID] = _uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"))
    title: Mapped[str] = mapped_column(Text, default="Chat")
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread", "thread_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id"))
    role: Mapped[str] = mapped_column(Text)  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (Index("ix_transcript_segments_order", "transcript_id", "idx"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcripts.id"))
    idx: Mapped[int] = mapped_column()
    start_ms: Mapped[int] = mapped_column()
    end_ms: Mapped[int] = mapped_column()
    speaker_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("speakers.id"), nullable=True)
    speaker_label: Mapped[str | None] = mapped_column(Text, nullable=True)  # raw diarizer label
    text: Mapped[str] = mapped_column(Text)
    words: Mapped[list | None] = mapped_column(JSONCol, nullable=True)  # [{w, s, e}] ms
