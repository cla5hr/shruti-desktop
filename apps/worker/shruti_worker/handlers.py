"""Aggregates all handler registrations — import this to get a fully populated registry."""

from shruti_worker.pipeline import asr, diarize, media, noop, summarize  # noqa: F401
from shruti_worker.registry import HANDLERS, Handler, ProgressFn  # noqa: F401
