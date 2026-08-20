"""Minutes-of-Meeting step. Runs AFTER the meeting is already `ready` — a failed
LLM call never takes down a perfectly good transcript; the Summary tab surfaces
the error and offers regenerate instead."""

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shruti_core.llm import chat, estimate_tokens
from shruti_core.models import Job, Meeting, Speaker, Summary, Transcript, TranscriptSegment
from shruti_core.settings import get_settings
from shruti_core.textfmt import transcript_lines  # noqa: F401  (re-export; tests use it here)
from shruti_worker.pipeline.prompts import (
    MERGE_INSTRUCTION,
    PARTIAL_INSTRUCTION,
    TEMPLATES,
    build_messages,
)
from shruti_worker.registry import ProgressFn, register

log = logging.getLogger("shruti.summarize")

# below this many transcribed words, don't ask the LLM for minutes — it will invent them
# (a real 4-segment mic test produced fully hallucinated minutes at the old value of 12)
MIN_WORDS_FOR_MINUTES = 25

# instruction fragments a small model sometimes echoes into its output; any line
# containing one is dropped deterministically (prompt-side rules are not enough)
_INSTRUCTION_ECHOES = (
    "produce minutes with exactly",
    "bullet list of the substantive discussion points",
    "bullets for decisions actually made",
    "only include items someone explicitly took on",
    "one bullet per item: **owner**",
    "what happens before/at the next meeting",
    "sentences on what the meeting covered",
    "no summary prose",
    "output markdown only",
)

_KNOWN_HEADINGS = (
    "## summary",
    "## key points",
    "## decisions",
    "## action items",
    "## next steps",
)


def clean_minutes(content: str, known_speaker_names: set[str]) -> str:
    """Deterministic cleanup of small-model artifacts (see colleague's app: 'fix it in
    post-processing, not the prompt'): echoed instructions, heading descriptions,
    double bullets, and bullets about speakers that don't exist in the transcript."""
    import re

    known_labels = {n.lower() for n in known_speaker_names}
    out: list[str] = []
    for line in content.splitlines():
        # "## Key Points — bullet list of..." → "## Key Points" (BEFORE the echo
        # filter, or the echoed description takes the whole heading down with it)
        if line.strip().lower().startswith(_KNOWN_HEADINGS) and ("—" in line or " - " in line):
            line = re.split(r"\s+[—-]\s+", line, maxsplit=1)[0]
        low = line.strip().lower()
        if any(fragment in low for fragment in _INSTRUCTION_ECHOES):
            continue
        # "- - [ ] task" → "- [ ] task"
        if line.lstrip().startswith("- - "):
            line = line.replace("- - ", "- ", 1)
        # a bullet about SPEAKER_N when no such speaker exists = invented content
        mentioned = {m.lower() for m in re.findall(r"speaker[_ ]?\d+", line, flags=re.IGNORECASE)}
        if mentioned and not any(
            m in known_labels or m.replace(" ", "_") in known_labels for m in mentioned
        ):
            continue
        out.append(line)
    return "\n".join(out).strip()


def split_for_budget(lines: list[str], budget_tokens: int) -> list[str]:
    """Split transcript lines into chunks that each fit the token budget."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for line in lines:
        t = estimate_tokens(line)
        if current and current_tokens + t > budget_tokens:
            chunks.append("\n".join(current))
            current, current_tokens = [], 0
        current.append(line)
        current_tokens += t
    if current:
        chunks.append("\n".join(current))
    return chunks


def generate_minutes(template_key: str, title: str, text: str, report_progress: ProgressFn) -> str:
    """Single-call when the transcript fits; two-level map-reduce when it doesn't."""
    settings = get_settings()
    budget = int(settings.llm_max_ctx * 0.6)  # leave room for instructions + output
    template = TEMPLATES[template_key]

    if estimate_tokens(text) <= budget:
        report_progress({"stage": "minutes", "mode": "single"})
        return chat(build_messages(template_key, title, text))

    chunks = split_for_budget(text.split("\n"), budget)
    partials: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        report_progress({"stage": "minutes", "mode": "map", "part": i, "total": len(chunks)})
        partials.append(
            chat(
                [
                    {"role": "system", "content": "You extract structured meeting notes."},
                    {
                        "role": "user",
                        "content": (
                            PARTIAL_INSTRUCTION.format(part=i, total=len(chunks))
                            + f"\n\nTranscript part:\n{chunk}"
                        ),
                    },
                ]
            )
        )
    report_progress({"stage": "minutes", "mode": "reduce"})
    merged_notes = "\n\n---\n\n".join(partials)
    return chat(
        [
            {"role": "system", "content": "You write final minutes of meeting."},
            {
                "role": "user",
                "content": (
                    MERGE_INSTRUCTION.format(instruction=template["instruction"])
                    + f"\n\nMeeting: {title}\n\nNotes:\n{merged_notes}"
                ),
            },
        ]
    )


def persist_summary(
    session: Session,
    meeting_id,
    transcript_id,
    content_md: str,
    *,
    template_key: str,
    model: str,
) -> Summary:
    session.execute(
        update(Summary)
        .where(Summary.meeting_id == meeting_id, Summary.is_active)
        .values(is_active=False)
    )
    summary = Summary(
        meeting_id=meeting_id,
        transcript_id=transcript_id,
        template_key=template_key,
        content_md=content_md,
        model=model,
        is_active=True,
    )
    session.add(summary)
    return summary


@register("summarize")
def handle_summarize(session: Session, job: Job, report_progress: ProgressFn) -> None:
    settings = get_settings()
    transcript_id = uuid.UUID(str(job.payload["transcript_id"]))
    template_key = job.payload.get("template", "standard")
    if template_key not in TEMPLATES:
        raise RuntimeError(f"unknown summary template {template_key!r}")

    transcript = session.get(Transcript, transcript_id)
    if transcript is None:
        raise RuntimeError(f"transcript {transcript_id} not found")
    meeting = session.get(Meeting, transcript.meeting_id)
    segments = list(
        session.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.idx)
        )
    )
    if not segments:
        raise RuntimeError("transcript has no segments to summarize")
    names = {
        s.id: s.display_name
        for s in session.scalars(select(Speaker).where(Speaker.meeting_id == meeting.id))
    }

    text = transcript_lines(segments, names)

    # Guard: an empty/near-silent recording produces a tiny transcript; a small LLM
    # will HALLUCINATE minutes with invented [m:ss] citations. Refuse instead.
    word_count = sum(len(seg.text.split()) for seg in segments)
    if word_count < MIN_WORDS_FOR_MINUTES:
        content = (
            "## Minutes unavailable\n\n"
            "Not enough speech was captured in this recording to generate minutes "
            f"(only {word_count} word{'s' if word_count != 1 else ''} transcribed). "
            "The audio may have been silent, too short, or the microphone/tab audio "
            "wasn't captured. Check the Transcript tab, then re-record or upload a "
            "clearer recording."
        )
        report_progress({"stage": "minutes", "mode": "skipped_low_content", "words": word_count})
    else:
        content = generate_minutes(template_key, meeting.title, text, report_progress)
        # checkpoint: a Stop pressed while the LLM was generating lands here, BEFORE
        # the old minutes get replaced (report_progress raises JobCancelled)
        report_progress({"stage": "persisting"})
        known = set(names.values()) | {seg.speaker_label for seg in segments if seg.speaker_label}
        content = clean_minutes(content, known)
        if len(content) < 20:
            # keep the previous minutes rather than replacing them with nothing
            raise RuntimeError(
                "the model returned empty/unusable minutes — try Regenerate, or a "
                "bigger model in Settings"
            )

    persist_summary(
        session,
        meeting.id,
        transcript.id,
        content,
        template_key=template_key,
        model=f"{settings.llm_model}" if settings.llm_mode == "live" else "stub",
    )
    session.commit()
