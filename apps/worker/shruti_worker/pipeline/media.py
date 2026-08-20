"""Media steps: extract_audio (ffmpeg) and waveform (peaks for the UI player).

Chain: extract_audio -> waveform -> asr
"""

import io
import json
import subprocess
import sys
import uuid
import wave

from sqlalchemy.orm import Session

from shruti_core import jobs
from shruti_core.models import Job, Meeting, Recording
from shruti_core.settings import get_settings
from shruti_core.storage import get_storage
from shruti_worker.registry import ProgressFn, register

WAVEFORM_BUCKETS = 1500

# the exe is windowed (no console): without this, every ffmpeg/ffprobe call pops
# open an empty cmd window on the user's screen
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class MediaError(RuntimeError):
    pass


# ffmpeg messages that mean the INPUT FILE itself is unreadable — retrying can
# never fix these, so the job fails immediately (a text file renamed .mp3 used to
# sit in QUEUED for ~15 minutes of backoff before the user saw anything)
_PERMANENT_FFMPEG_ERRORS = (
    "Invalid data found when processing input",
    "Error opening input",
)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        msg = f"{cmd[0]} failed (rc={proc.returncode}):\n{tail}"
        if any(marker in tail for marker in _PERMANENT_FFMPEG_ERRORS):
            raise jobs.PermanentJobError(
                f"this file isn't valid audio/video (ffmpeg can't read it)\n{msg}"
            )
        raise MediaError(msg)


def probe_duration_s(path: str) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise MediaError(f"ffprobe failed on {path}: {(proc.stderr or '')[-500:]}")
    return float(proc.stdout.strip())


def _load_recording(session: Session, job: Job) -> tuple[Recording, Meeting]:
    rec = session.get(Recording, uuid.UUID(str(job.payload["recording_id"])))
    if rec is None:
        raise MediaError(f"recording {job.payload.get('recording_id')} not found")
    meeting = session.get(Meeting, rec.meeting_id)
    if meeting is None:
        raise MediaError(f"meeting {rec.meeting_id} not found")
    return rec, meeting


@register("extract_audio")
def handle_extract_audio(session: Session, job: Job, report_progress: ProgressFn) -> None:
    """Original upload -> 16kHz mono WAV (ASR input) + AAC m4a (browser playback)."""
    rec, meeting = _load_recording(session, job)
    storage = get_storage()

    src = storage.path(rec.storage_key_original)
    wav_key = f"{meeting.id}/audio.wav"
    m4a_key = f"{meeting.id}/playback.m4a"
    wav_path = storage.path(wav_key)
    m4a_path = storage.path(m4a_key)
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    report_progress({"stage": "ffmpeg"})
    _run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path),
            "-vn", "-c:a", "aac", "-b:a", "96k", str(m4a_path),
        ]
    )  # fmt: skip

    duration = probe_duration_s(str(wav_path))
    rec.storage_key_audio_wav = wav_key
    rec.storage_key_playback = m4a_key
    rec.duration_s = duration
    meeting.duration_s = int(duration)
    meeting.status = "processing"
    session.commit()

    jobs.enqueue(
        session,
        "waveform",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
        dedupe_key=f"waveform:{rec.id}",
    )


@register("waveform")
def handle_waveform(session: Session, job: Job, report_progress: ProgressFn) -> None:
    """Precompute min/max peaks from the 16k mono WAV so the player renders instantly."""
    rec, meeting = _load_recording(session, job)
    storage = get_storage()
    if not rec.storage_key_audio_wav:
        raise MediaError("waveform requested before extract_audio produced audio.wav")

    report_progress({"stage": "peaks"})
    with wave.open(str(storage.path(rec.storage_key_audio_wav)), "rb") as wf:
        n_frames = wf.getnframes()
        rate = wf.getframerate()
        bucket = max(1, n_frames // WAVEFORM_BUCKETS)
        peaks: list[list[float]] = []
        while True:
            raw = wf.readframes(bucket)
            if not raw:
                break
            # 16-bit little-endian mono
            samples = memoryview(raw).cast("h")
            lo = min(samples) / 32768.0
            hi = max(samples) / 32768.0
            peaks.append([round(lo, 4), round(hi, 4)])

    peaks_key = f"{meeting.id}/peaks.json"
    payload = json.dumps(
        {"version": 1, "sample_rate": rate, "n_frames": n_frames, "peaks": peaks}
    ).encode()
    storage.save(peaks_key, io.BytesIO(payload))
    rec.storage_key_peaks = peaks_key
    session.commit()

    settings = get_settings()
    jobs.enqueue(
        session,
        "asr",
        queue="gpu",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
        dedupe_key=f"asr:{rec.id}:{settings.asr_model}",
    )
