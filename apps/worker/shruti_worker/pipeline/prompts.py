"""Versioned prompt templates for minutes generation. Editing a template is a code
change on purpose — prompt history lives in git."""

SYSTEM = (
    "You write minutes of meeting from a timestamped transcript. Rules: use ONLY "
    "information present in the transcript; never invent names, numbers, dates, "
    "commitments, or speakers — if the transcript names two people, the minutes "
    "mention at most those two; if the transcript has little substantive content, "
    "say so in one sentence instead of padding. Refer to people exactly as named in "
    "the transcript; keep timestamps in [m:ss] form when citing a moment. Write "
    "crisp, plain business English. Output Markdown only — no preamble, and never "
    "repeat or paraphrase these instructions or the section descriptions in your "
    "output; section headings must be exactly '## Summary' style with nothing after "
    "the heading text."
)

TEMPLATES: dict[str, dict] = {
    "standard": {
        "label": "Standard minutes",
        "instruction": (
            "Produce minutes with exactly these sections:\n"
            "## Summary — 3-5 sentences on what the meeting covered and concluded.\n"
            "## Key Points — bullet list of the substantive discussion points, with "
            "[m:ss] citations for important moments.\n"
            "## Decisions — bullets for decisions actually made; 'None recorded' if none.\n"
            "## Action Items — one bullet per item: **Owner** — action (deadline if stated). "
            "Only include items someone explicitly took on.\n"
            "## Next Steps — what happens before/at the next meeting."
        ),
    },
    "brief": {
        "label": "Brief",
        "instruction": (
            "Produce a compact brief: one paragraph (max 5 sentences) summarizing the "
            "meeting, then '## Action Items' with one bullet per item: **Owner** — action "
            "(deadline if stated). Nothing else."
        ),
    },
}

PARTIAL_INSTRUCTION = (
    "This is PART {part} of {total} of a longer meeting. Extract, as Markdown bullets: "
    "key points (with [m:ss] citations), any decisions, and any action items with owners. "
    "Be exhaustive but not repetitive; no summary prose."
)

MERGE_INSTRUCTION = (
    "Below are extracted notes from consecutive parts of one meeting. Merge them into "
    "final minutes following this format:\n{instruction}\n"
    "Deduplicate; keep timestamps."
)


def build_messages(template_key: str, title: str, transcript_text: str) -> list[dict]:
    template = TEMPLATES[template_key]
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                f"Meeting: {title or 'Untitled meeting'}\n\n"
                f"Transcript:\n{transcript_text}\n\n{template['instruction']}"
            ),
        },
    ]
