"""SQLite-backed job queue (ADR-002).

Semantics:
- claim:   atomic claim of the next runnable job (single worker on the desktop).
- retry:   exponential backoff (30s * 2^attempts, capped 1h), up to max_attempts.
- dedupe:  unique dedupe_key; re-enqueueing the same key is a silent no-op.
- stall:   running jobs whose heartbeat is stale get requeued by a maintenance sweep.

All functions take a Session and commit themselves (queue state changes must be
visible immediately; never leave a claim uncommitted).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from shruti_core.models import Job

BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    type: str,
    *,
    queue: str = "io",
    payload: dict | None = None,
    meeting_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    priority: int = 100,
    max_attempts: int = 5,
    run_after: datetime | None = None,
) -> Job | None:
    """Insert a job. Returns the Job, or None if dedupe_key already exists."""
    stmt = (
        sqlite_insert(Job)
        .values(
            id=uuid.uuid4(),
            type=type,
            queue=queue,
            payload=payload or {},
            meeting_id=meeting_id,
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts,
            run_after=run_after or _now(),
            status="queued",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
        .returning(Job.id)
    )
    job_id = session.execute(stmt).scalar_one_or_none()
    session.commit()
    if job_id is None:
        return None
    return session.get(Job, job_id)


def claim_next(session: Session, queues: list[str], worker_id: str) -> Job | None:
    """Atomically claim the next runnable job in the given queues."""
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.queue.in_(queues), Job.run_after <= _now())
        .order_by(Job.priority, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalars(stmt).first()
    if job is None:
        session.rollback()
        return None
    job.status = "running"
    job.locked_by = worker_id
    job.started_at = _now()
    job.heartbeat_at = _now()
    session.commit()
    return job


def heartbeat(session: Session, job_id: uuid.UUID, progress: dict | None = None) -> None:
    job = session.get(Job, job_id)
    if job is None or job.status != "running":
        return
    job.heartbeat_at = _now()
    if progress is not None:
        job.progress = progress
    session.commit()


def complete(session: Session, job: Job, progress: dict | None = None) -> None:
    job.status = "succeeded"
    job.finished_at = _now()
    job.error = None
    if progress is not None:
        job.progress = progress
    session.commit()


def fail(session: Session, job: Job, error: str) -> None:
    """Record a failure: retry with backoff, or mark failed when attempts are exhausted."""
    job.attempts += 1
    job.error = error[:10_000]
    job.locked_by = None
    job.heartbeat_at = None
    if job.attempts >= job.max_attempts:
        job.status = "failed"
        job.finished_at = _now()
    else:
        delay = min(BACKOFF_BASE_SECONDS * (2**job.attempts), BACKOFF_CAP_SECONDS)
        job.status = "queued"
        job.run_after = _now() + timedelta(seconds=delay)
    session.commit()


def requeue_stalled(session: Session, stale_seconds: int = 300) -> int:
    """Requeue running jobs whose worker stopped heartbeating (crash recovery).

    Counts as an attempt; exhausted jobs go to failed.
    """
    cutoff = _now() - timedelta(seconds=stale_seconds)
    stmt = (
        select(Job)
        .where(Job.status == "running", Job.heartbeat_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    stalled = list(session.scalars(stmt))
    for job in stalled:
        fail(session, job, error=f"stalled: no heartbeat since {job.heartbeat_at}")
    if not stalled:
        session.rollback()
    return len(stalled)
