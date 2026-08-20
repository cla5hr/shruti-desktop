"""Diarization step: who spoke when. Backends produce speaker turns; segments get
assigned by maximum temporal overlap. Runs after asr when DIARIZE_ENABLED.

Real backend is sherpa-onnx (pyannote-3.0 segmentation + WeSpeaker CAM++ in ONNX
form — no torch, no HF account). Tests use a canned backend.
"""

import concurrent.futures
import importlib.util
import logging
import multiprocessing
import uuid
from pathlib import Path
from typing import NamedTuple, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from shruti_core.models import Job, Speaker, Transcript, TranscriptSegment
from shruti_core.settings import Settings, get_settings
from shruti_core.storage import get_storage
from shruti_worker.pipeline.media import _load_recording
from shruti_worker.registry import ProgressFn, register

log = logging.getLogger("shruti.diarize")


class Turn(NamedTuple):
    start_ms: int
    end_ms: int
    label: str


class DiarizeBackend(Protocol):
    name: str

    def diarize(self, wav_path: Path, settings: Settings, num_speakers: int = 0) -> list[Turn]: ...


def _preload_ort() -> None:
    """Windows ships an ancient onnxruntime.dll in System32 (Windows ML). When
    running from source, sherpa's native lib would bind to it and abort with an
    API-version error — load the venv's own DLL into the process first. The exe
    is unaffected (PyInstaller resolves the bundled DLL before System32)."""
    import ctypes
    import sys

    if sys.platform != "win32":
        return
    try:
        import onnxruntime

        dll = Path(onnxruntime.__file__).parent / "capi" / "onnxruntime.dll"
        if dll.is_file():
            ctypes.WinDLL(str(dll))
    except Exception:
        pass  # best effort; the error message from sherpa is clear enough


def _run_sherpa(
    seg: str, emb: str, wav_path: str, num_speakers: int, threshold: float
) -> list[tuple[int, int, str]]:
    """Runs in a CHILD process. sherpa's process() holds the GIL for its entire
    multi-minute compute; in-process it froze the whole app (API, UI polling, even
    /healthz) for the duration — measured 9s of stall on an 8s run. The child's GIL
    is its own problem. Requires multiprocessing.freeze_support() in the exe entry."""
    import wave

    _preload_ort()
    import numpy as np
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg),
            num_threads=2,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb, num_threads=2),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers if num_speakers > 0 else -1, threshold=threshold
        ),
        min_duration_on=0.2,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)

    with wave.open(wav_path, "rb") as wf:
        if wf.getframerate() != sd.sample_rate or wf.getnchannels() != 1:
            raise RuntimeError(
                f"diarizer needs {sd.sample_rate} Hz mono wav, got "
                f"{wf.getframerate()} Hz / {wf.getnchannels()} ch"
            )
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    result = sd.process(samples).sort_by_start_time()
    return [(int(r.start * 1000), int(r.end * 1000), f"SPEAKER_{r.speaker:02d}") for r in result]


class SherpaBackend:
    """pyannote segmentation 3.0 + NeMo TitaNet-large via sherpa-onnx — no torch, no
    HF account, no token. ~107 MB of models download once from the sherpa-onnx GitHub
    releases into settings.sherpa_models_dir."""

    name = "sherpa"

    SEG_URL = (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
    )
    EMB_URL = (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-recongition-models/nemo_en_titanet_large.onnx"
    )
    # Embedder chosen by benchmark (2026-08-19) on LABELED 2- and 3-speaker fixtures,
    # both clean and degraded: TitaNet-large beat WeSpeaker CAM++ and ResNet34-LM.
    # Threshold re-swept 2026-08-20 against (a) the TTS fixture and (b) a real 6.6-min
    # 2-person Windows capture (mic + system audio, echoey):
    #   0.7 — fixture ✓ (2 spk, right turns) but the REAL capture shattered into 17
    #         raw clusters (5 survived the junk filter → "5 speakers" bug), and even
    #         pinning num_speakers=2 merged wrongly (317s/10s split);
    #   0.9 — fixture ✓ IDENTICAL turns, real capture ✓ resolves to the two true
    #         voices (215s/99s) with only sub-floor junk clusters;
    #   1.1 — fixture ✗ collapses both voices into one speaker.
    # The threshold scale is embedder-specific — re-sweep both files before changing.
    CLUSTER_THRESHOLD = 0.9

    def _dir(self, settings: Settings) -> Path:
        base = settings.sherpa_models_dir or "./data/models/sherpa"
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ensure_models(self, settings: Settings) -> tuple[Path, Path]:
        import tarfile
        import urllib.request

        d = self._dir(settings)
        seg = d / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
        emb = d / "nemo_en_titanet_large.onnx"
        if not seg.is_file():
            log.info("downloading pyannote segmentation onnx (~10 MB)")
            archive = d / "seg.tar.bz2"
            urllib.request.urlretrieve(self.SEG_URL, archive)
            with tarfile.open(archive, "r:bz2") as tf:
                tf.extractall(d)
            archive.unlink(missing_ok=True)
        if not emb.is_file():
            log.info("downloading NeMo TitaNet-large embeddings (~97 MB)")
            urllib.request.urlretrieve(self.EMB_URL, emb)
        if not seg.is_file() or not emb.is_file():
            raise RuntimeError("speaker model download finished but files are missing")
        return seg, emb

    def diarize(self, wav_path: Path, settings: Settings, num_speakers: int = 0) -> list[Turn]:
        if importlib.util.find_spec("sherpa_onnx") is None:
            raise RuntimeError("sherpa-onnx not installed - pip install sherpa-onnx")

        seg, emb = self._ensure_models(settings)

        ctx = multiprocessing.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            raw = pool.submit(
                _run_sherpa, str(seg), str(emb), str(wav_path), num_speakers,
                self.CLUSTER_THRESHOLD,
            ).result()  # fmt: skip
        return [Turn(*t) for t in raw]


BACKENDS: dict[str, DiarizeBackend] = {
    "sherpa": SherpaBackend(),
}


def drop_minor_speakers(turns: list[Turn], min_speech_s: float) -> list[Turn]:
    """Real meeting audio is full of junk (typing, notification sounds, breaths,
    crosstalk) that clusters into tiny singleton "speakers" — a 21-min test meeting
    produced 30+ labels with under 5s of speech each. Drop labels whose TOTAL speech
    is below min_speech_s; words in the dropped regions inherit the surrounding
    speaker via resegment_by_speaker's gap-fill. Pure function — unit-tested."""
    total: dict[str, float] = {}
    for t in turns:
        total[t.label] = total.get(t.label, 0.0) + (t.end_ms - t.start_ms) / 1000
    keep = {label for label, secs in total.items() if secs >= min_speech_s}
    if not keep and total:  # degenerate: everything tiny — keep the biggest voice
        keep = {max(total, key=lambda label: total[label])}
    return [t for t in turns if t.label in keep]


def _min_speech_s(duration_s: float) -> float:
    """Speech floor for a real speaker: 5% of the recording, clamped to [2s, 10s] —
    short clips keep quick exchanges, long meetings shed sub-10-second junk."""
    return min(10.0, max(2.0, 0.05 * duration_s))


def renumber_labels(turns: list[Turn]) -> list[Turn]:
    """After junk clusters are dropped, the surviving raw labels can be sparse
    (SPEAKER_01, SPEAKER_04) — renumber by first appearance so users always see
    SPEAKER_00, SPEAKER_01, … Pure function — unit-tested."""
    mapping: dict[str, str] = {}
    for t in turns:
        if t.label not in mapping:
            mapping[t.label] = f"SPEAKER_{len(mapping):02d}"
    return [Turn(t.start_ms, t.end_ms, mapping[t.label]) for t in turns]


def _best_label(start_ms: int, end_ms: int, turns: list[Turn]) -> str | None:
    """The speaker turn with maximum overlap over [start, end]; None if none overlap."""
    best_label: str | None = None
    best_overlap = 0
    for turn in turns:
        overlap = min(end_ms, turn.end_ms) - max(start_ms, turn.start_ms)
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = turn.label
    return best_label


def assign_speakers(segments: list[tuple[int, int]], turns: list[Turn]) -> list[str | None]:
    """Segment-level assignment (fallback when word timings are absent). Pure, tested."""
    return [_best_label(s, e, turns) for s, e in segments]


def resegment_by_speaker(segments: list[dict], turns: list[Turn], gap_ms: int = 1200) -> list[dict]:
    """Re-split transcript segments so each output segment is a SINGLE speaker.

    Assigns speakers at the WORD level (using Whisper word timestamps) so a speaker
    change mid-sentence lands on a real boundary — fixes "trailing words attributed to
    the next speaker". Falls back to whole-segment assignment when a segment has no
    word timings (e.g. user-edited text). Pure function — golden-tested.

    Input segments: dicts with start_ms, end_ms, text, words (list of {w,s,e} or None).
    Output: dicts with start_ms, end_ms, text, speaker_label, words.
    """
    # 1) flatten to a stream of [start, end, text, speaker] at word granularity
    labeled: list[list] = []
    for seg in segments:
        words = seg.get("words")
        if words:
            for w in words:
                labeled.append([w["s"], w["e"], w["w"], _best_label(w["s"], w["e"], turns)])
        else:
            labeled.append(
                [
                    seg["start_ms"],
                    seg["end_ms"],
                    seg["text"],
                    _best_label(seg["start_ms"], seg["end_ms"], turns),
                ]
            )

    # 1b) fill None labels (words in gaps BETWEEN pyannote turns) from the nearest
    # labeled neighbour, so they inherit the surrounding speaker instead of becoming
    # tiny blank fragments. Forward-fill, then back-fill any leading Nones.
    last: str | None = None
    for w in labeled:
        if w[3] is None:
            w[3] = last
        else:
            last = w[3]
    nxt: str | None = None
    for w in reversed(labeled):
        if w[3] is None:
            w[3] = nxt
        else:
            nxt = w[3]

    # 2) group consecutive same-speaker words; break on speaker change or a real pause
    out: list[dict] = []
    cur: dict | None = None
    for start, end, text, label in labeled:
        same = cur is not None and label == cur["speaker_label"] and start - cur["end_ms"] <= gap_ms
        if same:
            cur["end_ms"] = end
            cur["words"].append({"w": text, "s": start, "e": end})
        else:
            if cur is not None:
                out.append(cur)
            cur = {
                "start_ms": start,
                "end_ms": end,
                "speaker_label": label,
                "words": [{"w": text, "s": start, "e": end}],
            }
    if cur is not None:
        out.append(cur)

    for seg in out:
        seg["text"] = " ".join(w["w"].strip() for w in seg["words"]).strip()
    return out


@register("diarize")
def handle_diarize(session: Session, job: Job, report_progress: ProgressFn) -> None:
    settings = get_settings()
    rec, meeting = _load_recording(session, job)
    if not rec.storage_key_audio_wav:
        raise RuntimeError("diarize requested before extract_audio produced audio.wav")

    # ALWAYS operate on the meeting's active transcript, resolved at RUN time.
    # Jobs carry the transcript that was active when they were ENQUEUED; if the user
    # retranscribes (or re-detects) while another run is in flight, the stale job
    # would rebuild an old transcript and — because the rebuild nulls speaker_id
    # across every transcript version first — wipe the fresh one's speakers too.
    transcript = session.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting.id, Transcript.is_active)
    ).first()
    if transcript is None:
        raise RuntimeError(f"meeting {meeting.id} has no active transcript to diarize")
    payload_tid = job.payload.get("transcript_id")
    if payload_tid and uuid.UUID(str(payload_tid)) != transcript.id:
        log.info(
            "diarize job %s targeted stale transcript %s; using active %s",
            job.id, payload_tid, transcript.id,
        )

    backend = BACKENDS.get(settings.diarize_backend)
    if backend is None:
        raise RuntimeError(f"unknown diarize backend {settings.diarize_backend!r}")

    # head-count priority: this job's explicit request (Speakers panel) > Settings.
    # Pinning the real head-count beats any auto-clustering heuristic.
    raw_ns = job.payload.get("num_speakers")
    num_speakers = int(raw_ns) if raw_ns is not None else int(settings.diarize_num_speakers or 0)

    report_progress({"stage": "diarizing"})
    turns = backend.diarize(
        get_storage().path(rec.storage_key_audio_wav), settings, num_speakers
    )

    # auto-detect mode only: a user-pinned head-count already forces the cluster count
    if not num_speakers:
        turns = drop_minor_speakers(turns, _min_speech_s(rec.duration_s or 0.0))
        turns = renumber_labels(turns)

    old_segments = list(
        session.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.idx)
        )
    )
    # WORD-LEVEL re-segmentation: split at real speaker boundaries so trailing words
    # don't leak onto the next speaker (see resegment_by_speaker).
    report_progress({"stage": "assigning", "turns": len(turns)})
    as_dicts = [
        {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text, "words": s.words}
        for s in old_segments
    ]
    new_segments = resegment_by_speaker(as_dicts, turns)

    # speakers in order of first appearance in the re-segmented transcript
    ordered_labels: list[str] = []
    for seg in new_segments:
        lab = seg["speaker_label"]
        if lab and lab not in ordered_labels:
            ordered_labels.append(lab)

    # idempotent rebuild. Order matters for the FK segment.speaker_id -> speakers.id:
    #   1) null speaker_id on ALL meeting segments (across transcript versions)
    #   2) delete this transcript's segments  3) delete the meeting's speakers
    meeting_transcripts = select(Transcript.id).where(Transcript.meeting_id == meeting.id)
    session.execute(
        update(TranscriptSegment)
        .where(TranscriptSegment.transcript_id.in_(meeting_transcripts))
        .values(speaker_id=None)
    )
    session.execute(
        delete(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id)
    )
    session.execute(delete(Speaker).where(Speaker.meeting_id == meeting.id))
    session.flush()

    speakers = {
        label: Speaker(meeting_id=meeting.id, display_name=label, source_label=label)
        for label in ordered_labels
    }
    session.add_all(speakers.values())
    session.flush()

    for idx, seg in enumerate(new_segments):
        label = seg["speaker_label"]
        session.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                idx=idx,
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                text=seg["text"],
                speaker_label=label,
                speaker_id=speakers[label].id if label else None,
                words=seg["words"] or None,
            )
        )

    meeting.status = "ready"
    session.commit()

    from shruti_core import jobs

    jobs.enqueue(
        session,
        "summarize",
        queue="io",
        meeting_id=meeting.id,
        payload={"transcript_id": str(transcript.id), "template": "standard"},
        # scoped to THIS diarize run: dedupe keys are unique forever, so a key
        # without job.id would silently skip the summary refresh on every
        # re-detect after the first (speakers change, minutes stay stale)
        dedupe_key=f"summarize:{transcript.id}:standard:{job.id}",
    )
