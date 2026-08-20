import time

from sqlalchemy.orm import Session

from shruti_core.models import Job
from shruti_worker.registry import ProgressFn, register


@register("noop")
def handle_noop(session: Session, job: Job, report_progress: ProgressFn) -> None:
    """Walking-skeleton job: proves API -> queue -> worker -> DB -> UI round-trip."""
    report_progress({"step": "started"})
    time.sleep(0.2)
    report_progress({"step": "done", "echo": job.payload})
