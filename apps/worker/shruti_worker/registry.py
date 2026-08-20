"""Job handler registry.

Handler contract: `handler(session, job, report_progress)` — runs to completion or raises.
Handlers must be idempotent (safe to re-run after a crash/retry): they overwrite their own
outputs and enqueue the next step with a dedupe_key.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from shruti_core.models import Job

ProgressFn = Callable[[dict], None]
Handler = Callable[[Session, Job, ProgressFn], None]

HANDLERS: dict[str, Handler] = {}


def register(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco
