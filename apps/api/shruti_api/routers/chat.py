"""Q&A over a meeting. Answers stream over SSE; the whole active transcript is
stuffed into context (no RAG — a 30-60min meeting fits comfortably), degrading
to minutes + transcript tail for very long meetings."""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_core.db import new_session
from shruti_core.llm import LLMError, chat_stream, estimate_tokens
from shruti_core.models import (
    ChatMessage,
    ChatThread,
    Meeting,
    Speaker,
    Summary,
    Transcript,
    TranscriptSegment,
)
from shruti_core.settings import get_settings
from shruti_core.textfmt import transcript_lines

router = APIRouter(prefix="/api", tags=["chat"])

Db = Annotated[Session, Depends(get_db)]

SYSTEM = (
    "You answer questions about one specific meeting, using ONLY the meeting context "
    "provided below. You are not a general assistant: if a question is unrelated to "
    "this meeting (general knowledge, math, definitions, coding, anything not "
    "discussed in it), do NOT answer it — reply that you only answer questions about "
    "this meeting. If the question is about the meeting but the answer is not in the "
    "context, say you can't find it in this meeting. Never use outside knowledge to "
    "fill gaps. Refer to people by their names as given. When you reference a moment, "
    "cite its timestamp in square brackets like [2:15] so the reader can jump to it. "
    "Be direct and concise."
)

HISTORY_LIMIT = 8


def build_context(db: Session, meeting: Meeting) -> str:
    """Full transcript when it fits; otherwise minutes + the transcript tail."""
    transcript = db.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.is_active)
    ).first()
    if transcript is None:
        raise HTTPException(status_code=409, detail="no transcript to chat about yet")
    segments = list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.idx)
        )
    )
    names = {
        s.id: s.display_name
        for s in db.scalars(select(Speaker).where(Speaker.meeting_id == meeting.id))
    }
    full = transcript_lines(segments, names)
    budget = int(get_settings().llm_max_ctx * 0.55)
    if estimate_tokens(full) <= budget:
        return f"Meeting: {meeting.title}\n\nTranscript:\n{full}"

    # degradation: minutes (if any) + as much of the tail as fits
    summary = db.scalars(
        select(Summary).where(Summary.meeting_id == meeting.id, Summary.is_active)
    ).first()
    summary_md = summary.content_md if summary else "(no minutes available)"
    remaining = budget - estimate_tokens(summary_md)
    lines = full.split("\n")
    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        t = estimate_tokens(line)
        if used + t > max(remaining, 0):
            break
        tail.append(line)
        used += t
    tail.reverse()
    return (
        f"Meeting: {meeting.title}\n\nMinutes of the full meeting:\n{summary_md}\n\n"
        f"Transcript (final portion only; earlier parts are covered by the minutes above):\n"
        + "\n".join(tail)
    )


def thread_public(t: ChatThread) -> dict:
    return {
        "id": str(t.id),
        "title": t.title,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def message_public(m: ChatMessage) -> dict:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/meetings/{meeting_id}/threads")
def list_threads(meeting_id: uuid.UUID, db: Db) -> list[dict]:
    rows = db.scalars(
        select(ChatThread)
        .where(ChatThread.meeting_id == meeting_id)
        .order_by(ChatThread.created_at)
    ).all()
    return [thread_public(t) for t in rows]


class ThreadCreateBody(BaseModel):
    title: str = "Chat"


@router.post("/meetings/{meeting_id}/threads", status_code=201)
def create_thread(meeting_id: uuid.UUID, body: ThreadCreateBody, db: Db) -> dict:
    if db.get(Meeting, meeting_id) is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    thread = ChatThread(meeting_id=meeting_id, title=body.title.strip() or "Chat")
    db.add(thread)
    db.commit()
    return thread_public(thread)


@router.get("/threads/{thread_id}/messages")
def list_messages(thread_id: uuid.UUID, db: Db) -> list[dict]:
    if db.get(ChatThread, thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at)
    ).all()
    return [message_public(m) for m in rows]


class AskBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/threads/{thread_id}/messages")
def ask(thread_id: uuid.UUID, body: AskBody, db: Db) -> StreamingResponse:
    thread = db.get(ChatThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    meeting = db.get(Meeting, thread.meeting_id)

    # guard: don't let the model answer (hallucinate) when there's no real transcript
    transcript = db.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.is_active)
    ).first()
    if transcript is not None:
        words = db.scalars(
            select(TranscriptSegment.text).where(TranscriptSegment.transcript_id == transcript.id)
        ).all()
        if sum(len(t.split()) for t in words) < 12:
            user_msg = ChatMessage(thread_id=thread.id, role="user", content=body.content.strip())
            db.add(user_msg)
            db.commit()

            def empty_stream():
                msg = (
                    "There isn't enough transcript from this meeting to answer — the "
                    "recording may have been silent or too short."
                )
                yield _sse({"user_message_id": str(user_msg.id)})
                yield _sse({"delta": msg})
                asst = ChatMessage(
                    thread_id=thread.id, role="assistant", content=msg, model="guard"
                )
                db.add(asst)
                db.commit()
                yield _sse({"done": True, "message_id": str(asst.id)})

            return StreamingResponse(empty_stream(), media_type="text/event-stream")

    context = build_context(db, meeting)
    history = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()[::-1]

    user_msg = ChatMessage(thread_id=thread.id, role="user", content=body.content.strip())
    db.add(user_msg)
    db.commit()

    messages = [
        {"role": "system", "content": f"{SYSTEM}\n\n{context}"},
        *({"role": m.role, "content": m.content} for m in history),
        {"role": "user", "content": user_msg.content},
    ]
    settings = get_settings()
    thread_id_val = thread.id
    user_msg_id = user_msg.id

    def event_stream():
        yield _sse({"user_message_id": str(user_msg_id)})
        parts: list[str] = []
        try:
            for delta in chat_stream(messages):
                parts.append(delta)
                yield _sse({"delta": delta})
        except LLMError as exc:
            yield _sse({"error": str(exc)})
            return
        full = "".join(parts)
        session = new_session()  # request session may close before we finish streaming
        try:
            assistant = ChatMessage(
                thread_id=thread_id_val,
                role="assistant",
                content=full,
                model=settings.llm_model if settings.llm_mode == "live" else "stub",
            )
            session.add(assistant)
            session.commit()
            yield _sse({"done": True, "message_id": str(assistant.id)})
        finally:
            session.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
