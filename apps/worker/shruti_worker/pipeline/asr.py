"""ASR step: pluggable backends produce a normalized AsrResult, persisted as a
versioned transcript. Backend/model/device selection is pure config (settings)."""

import logging
from pathlib import Path
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from shruti_core.asr_types import AsrResult, AsrSegment, AsrWord
from shruti_core.models import Job, Transcript, TranscriptSegment
from shruti_core.settings import Settings, get_settings
from shruti_core.storage import get_storage
from shruti_worker.pipeline.media import _load_recording
from shruti_worker.registry import ProgressFn, register

log = logging.getLogger("shruti.asr")


class AsrBackend(Protocol):
    name: str

    def transcribe(
        self, wav_path: Path, settings: Settings, progress: ProgressFn | None = None
    ) -> AsrResult: ...


class FasterWhisperBackend:
    name = "faster_whisper"
    _cache: dict[tuple[str, str, str], object] = {}

    def _model(self, settings: Settings):
        key = (settings.asr_model, settings.asr_device, settings.asr_compute_type)
        if key not in self._cache:
            import os

            from faster_whisper import WhisperModel  # heavy import, deferred

            log.info("loading whisper model %s (%s/%s)", *key)
            self._cache[key] = WhisperModel(
                settings.asr_model,
                device=settings.asr_device,
                compute_type=settings.asr_compute_type,
                # default is 4 threads; use the machine — a 21-min meeting on 'small'
                # takes ~30 min at 4 threads on a laptop, every core helps
                cpu_threads=max(4, (os.cpu_count() or 4) - 2),
            )
        return self._cache[key]

    def transcribe(
        self, wav_path: Path, settings: Settings, progress: ProgressFn | None = None
    ) -> AsrResult:
        model = self._model(settings)
        segments_iter, info = model.transcribe(
            str(wav_path),
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )
        total_s = float(getattr(info, "duration", 0) or 0)
        last_pct = -1
        segments: list[AsrSegment] = []
        for seg in segments_iter:
            if progress is not None and total_s:
                pct = min(99, int(seg.end / total_s * 100))
                if pct > last_pct:
                    last_pct = pct
                    progress({"pct": pct})
            words = [
                AsrWord(word=w.word.strip(), start_ms=int(w.start * 1000), end_ms=int(w.end * 1000))
                for w in (seg.words or [])
            ]
            segments.append(
                AsrSegment(
                    start_ms=int(seg.start * 1000),
                    end_ms=int(seg.end * 1000),
                    text=seg.text.strip(),
                    words=words,
                )
            )
        return AsrResult(language=info.language, segments=segments)


BACKENDS: dict[str, AsrBackend] = {
    "faster_whisper": FasterWhisperBackend(),
}


def persist_transcript(
    session: Session,
    meeting_id,
    result: AsrResult,
    *,
    kind: str,
    engine: str,
    model: str,
) -> Transcript:
    """Single-commit persistence: deactivate old transcripts, insert new one + segments."""
    session.execute(
        update(Transcript)
        .where(Transcript.meeting_id == meeting_id, Transcript.is_active)
        .values(is_active=False)
    )
    transcript = Transcript(
        meeting_id=meeting_id,
        kind=kind,
        engine=engine,
        model=model,
        language=result.language,
        is_active=True,
    )
    session.add(transcript)
    session.flush()
    for i, seg in enumerate(result.segments):
        session.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                idx=i,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=seg.text,
                words=[{"w": w.word, "s": w.start_ms, "e": w.end_ms} for w in seg.words] or None,
            )
        )
    return transcript


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _friendly_download_error(model: str, exc: BaseException) -> str:
    """Users see this in the Settings page — say what to DO, not a traceback."""
    text = f"{exc.__class__.__name__}: {exc}".lower()
    offline_markers = (
        "getaddrinfo",
        "connecterror",
        "could not be reached",
        "connection refused",
        "timed out",
        "temporary failure in name resolution",
    )
    if any(m in text for m in offline_markers):
        return (
            f"Couldn't reach huggingface.co to download '{model}' — this computer looks "
            "offline, or a VPN/proxy is blocking it. Connect to the internet and press "
            "Save again; the download resumes where it left off."
        )
    return f"Model download failed: {exc}"


@register("asr_prefetch")
def handle_asr_prefetch(session: Session, job: Job, report_progress: ProgressFn) -> None:
    """Download a whisper model NOW (triggered from Settings) instead of silently on
    the first transcription. Progress = growth of the HF cache dir, reported so the
    Settings page can draw a real bar. Idempotent: cached model completes instantly."""
    import os
    import threading

    from faster_whisper.utils import download_model

    model = str(job.payload["model"])
    total_mb = int(job.payload.get("total_mb") or 0)
    cache = Path(
        os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    ).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    baseline = _dir_size(cache)

    errors: list[BaseException] = []

    def work() -> None:
        try:
            download_model(model)
        except BaseException as exc:  # surfaced below as the job error
            errors.append(exc)

    t = threading.Thread(target=work, daemon=True, name=f"prefetch-{model}")
    t.start()
    while t.is_alive():
        got_mb = max(0, (_dir_size(cache) - baseline)) // 1_000_000
        pct = min(99, int(got_mb * 100 / total_mb)) if total_mb else 0
        report_progress(
            {"stage": "downloading", "model": model, "downloaded_mb": got_mb, "pct": pct}
        )
        t.join(timeout=1.0)
    if errors:
        raise RuntimeError(_friendly_download_error(model, errors[0])) from errors[0]
    report_progress({"stage": "done", "model": model, "pct": 100})


@register("asr")
def handle_asr(session: Session, job: Job, report_progress: ProgressFn) -> None:
    settings = get_settings()
    rec, meeting = _load_recording(session, job)
    if not rec.storage_key_audio_wav:
        raise RuntimeError("asr requested before extract_audio produced audio.wav")

    backend = BACKENDS.get(settings.asr_backend)
    if backend is None:
        raise RuntimeError(f"unknown ASR backend {settings.asr_backend!r}")

    if settings.asr_device == "cuda":
        # HARD gate, checked before ctranslate2 ever touches CUDA: with a GPU present
        # but its libraries missing, ct2's lazy DLL load can HANG (not raise) inside
        # the Windows loader — the thread parks at 0% CPU forever and the exception
        # fallback below never fires. The ctypes probe cannot hang.
        from shruti_core.cuda import cuda_usable

        if not cuda_usable():
            log.warning("asr_device=cuda but CUDA is not usable here — using CPU")
            settings = settings.model_copy(update={"asr_device": "cpu", "asr_compute_type": "int8"})

    report_progress({"stage": "transcribing", "model": settings.asr_model})
    wav_path = get_storage().path(rec.storage_key_audio_wav)

    def on_progress(p: dict) -> None:
        report_progress({"stage": "transcribing", "model": settings.asr_model, **p})

    try:
        result = backend.transcribe(wav_path, settings, progress=on_progress)
    except RuntimeError as exc:
        # second layer: CUDA that passed the probe can still fail at compute time
        # (driver issues, OOM) — fall back to CPU in the same run instead of
        # crash-looping through retries while the meeting sits in "processing"
        if settings.asr_device == "cuda" and any(
            marker in str(exc).lower() for marker in ("cublas", "cudnn", "cuda")
        ):
            log.warning("GPU transcription failed (%s) — falling back to CPU", exc)
            report_progress(
                {"stage": "transcribing", "model": settings.asr_model, "note": "GPU failed → CPU"}
            )
            cpu = settings.model_copy(update={"asr_device": "cpu", "asr_compute_type": "int8"})
            result = backend.transcribe(wav_path, cpu, progress=on_progress)
        else:
            raise

    report_progress({"stage": "persisting", "segments": len(result.segments)})
    transcript = persist_transcript(
        session,
        meeting.id,
        result,
        kind="whisper_raw",
        engine=backend.name,
        model=settings.asr_model,
    )
    if not settings.diarize_enabled:
        meeting.status = "ready"
    session.commit()

    from shruti_core import jobs

    if settings.diarize_enabled:
        jobs.enqueue(
            session,
            "diarize",
            queue="gpu",
            meeting_id=meeting.id,
            payload={"recording_id": str(rec.id), "transcript_id": str(transcript.id)},
            dedupe_key=f"diarize:{transcript.id}",
        )
    else:
        # minutes run after `ready` — an LLM hiccup never blocks the transcript
        jobs.enqueue(
            session,
            "summarize",
            queue="io",
            meeting_id=meeting.id,
            payload={"transcript_id": str(transcript.id), "template": "standard"},
            dedupe_key=f"summarize:{transcript.id}:standard",
        )
