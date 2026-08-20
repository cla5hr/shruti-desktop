"""Shared builders for worker pipeline tests."""

import io
import math
import wave

from shruti_core.models import Meeting, Recording
from shruti_core.storage import get_storage


def make_wav_bytes(seconds: float = 1.0, freq: int = 440) -> bytes:
    """16kHz mono 16-bit sine — a valid ASR-input-shaped WAV."""
    rate = 16000
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            val = int(20000 * math.sin(2 * math.pi * freq * i / rate))
            frames += val.to_bytes(2, "little", signed=True)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def make_meeting_with_upload(db, content: bytes, ext: str = ".wav"):
    meeting = Meeting(source="upload", title="t")
    db.add(meeting)
    db.flush()
    key = f"{meeting.id}/original{ext}"
    get_storage().save(key, io.BytesIO(content))
    rec = Recording(meeting_id=meeting.id, storage_key_original=key, size_bytes=len(content))
    db.add(rec)
    db.commit()
    return meeting, rec
