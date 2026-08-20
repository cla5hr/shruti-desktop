"""Transcript text formatting shared by the minutes job (worker) and Q&A chat (api)."""

from shruti_core.models import TranscriptSegment


def clock(ms: int) -> str:
    total_s = ms // 1000
    h, m, s = total_s // 3600, (total_s % 3600) // 60, total_s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def transcript_lines(segments: list[TranscriptSegment], names: dict) -> str:
    lines = []
    for seg in segments:
        who = names.get(seg.speaker_id) or seg.speaker_label or "Speaker"
        lines.append(f"[{clock(seg.start_ms)}] {who}: {seg.text}")
    return "\n".join(lines)
