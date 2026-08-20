"""Normalized ASR result schema — every ASR backend (faster-whisper on the laptop,
WhisperX on the Spark) converts its output into this shape before persistence.
Times are integer milliseconds from the start of the audio."""

from pydantic import BaseModel


class AsrWord(BaseModel):
    word: str
    start_ms: int
    end_ms: int


class AsrSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    words: list[AsrWord] = []


class AsrResult(BaseModel):
    language: str | None = None
    segments: list[AsrSegment] = []

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)
