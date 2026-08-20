"""Q&A chat: SSE streaming with the stub LLM, persistence, history, degradation."""

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_speakers import seed_meeting

from shruti_api.main import app
from shruti_api.routers import chat as chat_router
from shruti_core.models import ChatMessage, Meeting, Summary, Transcript
from shruti_core.settings import get_settings


def _events(sse_text: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in sse_text.splitlines()
        if line.startswith("data:")
    ]


def _make_thread(client, meeting_id: str) -> str:
    resp = client.post(f"/api/meetings/{meeting_id}/threads", json={})
    assert resp.status_code == 201
    return resp.json()["id"]


def test_thread_create_and_list(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)
    tid = _make_thread(client, str(meeting.id))
    listed = client.get(f"/api/meetings/{meeting.id}/threads").json()
    assert [t["id"] for t in listed] == [tid]
    assert client.get(f"/api/threads/{tid}/messages").json() == []


def test_ask_streams_and_persists(db):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)
    tid = _make_thread(client, str(meeting.id))

    resp = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "What is the immediate priority?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _events(resp.text)
    assert "user_message_id" in events[0]
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas, "no streamed deltas"
    assert events[-1].get("done") is True

    msgs = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.thread_id == uuid.UUID(tid))
        .order_by(ChatMessage.created_at)
    ).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "".join(deltas)
    assert "[0:28]" in msgs[1].content  # stub cites a timestamp


def test_prompt_contains_transcript_and_history(db, monkeypatch):
    meeting, *_ = seed_meeting(db)
    client = TestClient(app)
    tid = _make_thread(client, str(meeting.id))

    captured: list[list[dict]] = []

    def fake_stream(messages, **kw):
        captured.append(messages)
        yield "ok"

    monkeypatch.setattr(chat_router, "chat_stream", fake_stream)

    client.post(f"/api/threads/{tid}/messages", json={"content": "first question"})
    client.post(f"/api/threads/{tid}/messages", json={"content": "second question"})

    first, second = captured
    assert "Good morning everyone." in first[0]["content"]  # transcript in system msg
    roles = [m["role"] for m in second]
    assert roles == ["system", "user", "assistant", "user"]  # history carried
    assert second[1]["content"] == "first question"
    assert second[-1]["content"] == "second question"


def test_ask_409_without_transcript(db):
    meeting = Meeting(source="upload", title="empty", status="ready")
    db.add(meeting)
    db.commit()
    client = TestClient(app)
    tid = _make_thread(client, str(meeting.id))
    resp = client.post(f"/api/threads/{tid}/messages", json={"content": "hi"})
    assert resp.status_code == 409


def test_context_degrades_to_minutes_plus_tail(db, monkeypatch):
    meeting, *_ = seed_meeting(db)
    transcript = db.scalars(select(Transcript)).one()
    db.add(
        Summary(
            meeting_id=meeting.id,
            transcript_id=transcript.id,
            content_md="## Summary\n\nThe minutes.",
            model="stub",
        )
    )
    db.commit()

    # budget = 0.55*80 = 44 tokens: whole transcript (~60) won't fit,
    # but the last line (~27) will — exercising the tail-selection path
    monkeypatch.setenv("LLM_MAX_CTX", "80")
    get_settings.cache_clear()
    context = chat_router.build_context(db, meeting)
    get_settings.cache_clear()

    assert "Minutes of the full meeting:" in context
    assert "The minutes." in context
    assert "final portion only" in context
    # the tail keeps the LAST segment, not the first
    assert "bench" in context.lower()
    assert "Good morning everyone." not in context
