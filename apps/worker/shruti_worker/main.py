"""Worker: claims jobs from the Postgres queue and dispatches to handlers.

Run: uv run python -m shruti_worker.main
Queues claimed come from settings.worker_queues (comma-separated).
"""

import logging
import socket
import threading
import time
import traceback

from shruti_core import jobs
from shruti_core.db import new_session
from shruti_core.models import Job, Meeting
from shruti_core.settings import get_settings
from shruti_worker.handlers import HANDLERS

log = logging.getLogger("shruti.worker")

STALL_SWEEP_INTERVAL_S = 60


class _Heartbeat(threading.Thread):
    """Keeps job.heartbeat_at fresh while a handler runs (own DB session)."""

    def __init__(self, job_id, interval_s: float):
        super().__init__(daemon=True)
        self.job_id = job_id
        self.interval_s = interval_s
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval_s):
            session = new_session()
            try:
                jobs.heartbeat(session, self.job_id)
            except Exception:
                log.warning("heartbeat failed for %s", self.job_id, exc_info=True)
            finally:
                session.close()


def _mark_meeting_failed_if_terminal(session, job: Job) -> None:
    """A permanently failed pipeline job takes its meeting to 'failed' (surfaced in UI)."""
    if job.status == "failed" and job.meeting_id is not None:
        meeting = session.get(Meeting, job.meeting_id)
        if meeting is not None and meeting.status not in ("failed", "ready"):
            meeting.status = "failed"
            session.commit()


def run_one(session, job: Job) -> None:
    handler = HANDLERS.get(job.type)
    if handler is None:
        jobs.fail(session, job, f"no handler registered for job type '{job.type}'")
        _mark_meeting_failed_if_terminal(session, job)
        return

    def report_progress(progress: dict) -> None:
        jobs.heartbeat(session, job.id, progress=progress)

    hb = _Heartbeat(job.id, get_settings().worker_heartbeat_seconds)
    hb.start()
    try:
        handler(session, job, report_progress)
        jobs.complete(session, job)
        log.info("job %s (%s) succeeded", job.id, job.type)
    except Exception:
        err = traceback.format_exc()
        log.error("job %s (%s) failed:\n%s", job.id, job.type, err)
        # a failed handler may leave half-done mutations in the session; without this
        # rollback, jobs.fail()'s commit would persist them (e.g. a summary deactivated
        # but its replacement never written)
        session.rollback()
        jobs.fail(session, job, err)
        _mark_meeting_failed_if_terminal(session, job)
    finally:
        hb.stop_event.set()
        hb.join(timeout=2)


def run_until_idle(queues: list[str] | None = None, max_jobs: int = 100) -> int:
    """Process queued jobs until none are runnable. Used by tests and smoke flows."""
    settings = get_settings()
    qs = queues or [q.strip() for q in settings.worker_queues.split(",") if q.strip()]
    count = 0
    while count < max_jobs:
        session = new_session()
        try:
            job = jobs.claim_next(session, qs, worker_id="inline")
            if job is None:
                return count
            run_one(session, job)
            count += 1
        finally:
            session.close()
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    queues = [q.strip() for q in settings.worker_queues.split(",") if q.strip()]
    worker_id = f"{socket.gethostname()}:{threading.get_native_id()}"
    log.info("worker %s starting, queues=%s", worker_id, queues)

    last_sweep = 0.0
    while True:
        try:
            session = new_session()
            try:
                if time.monotonic() - last_sweep > STALL_SWEEP_INTERVAL_S:
                    n = jobs.requeue_stalled(session, settings.job_stale_seconds)
                    if n:
                        log.warning("requeued %d stalled job(s)", n)
                    last_sweep = time.monotonic()

                job = jobs.claim_next(session, queues, worker_id)
                if job is None:
                    time.sleep(settings.worker_poll_seconds)
                else:
                    run_one(session, job)
            finally:
                session.close()
        except KeyboardInterrupt:
            log.info("worker stopping (interrupt)")
            break
        except Exception:
            log.error("worker loop error", exc_info=True)
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
