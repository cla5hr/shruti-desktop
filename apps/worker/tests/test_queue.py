"""Queue spec — these tests define the contract every pipeline step relies on."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from shruti_core import jobs


def aware(dt: datetime) -> datetime:
    """SQLite hands back naive UTC datetimes — normalize before comparing."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def test_enqueue_and_claim_roundtrip(db):
    created = jobs.enqueue(db, "noop", queue="io", payload={"x": 1})
    assert created is not None and created.status == "queued"

    job = jobs.claim_next(db, ["io"], worker_id="w1")
    assert job is not None
    assert job.id == created.id
    assert job.status == "running"
    assert job.locked_by == "w1"
    assert job.heartbeat_at is not None

    # nothing else to claim
    assert jobs.claim_next(db, ["io"], worker_id="w2") is None


def test_claim_respects_queue_filter(db):
    jobs.enqueue(db, "noop", queue="gpu")
    assert jobs.claim_next(db, ["io"], worker_id="w1") is None
    assert jobs.claim_next(db, ["gpu"], worker_id="w1") is not None


def test_claimed_job_is_not_claimable_again(db):
    """Once claimed (status=running), a job is invisible to further claims."""
    created = jobs.enqueue(db, "noop", queue="io")
    first = jobs.claim_next(db, ["io"], worker_id="w1")
    assert first is not None and first.id == created.id
    assert jobs.claim_next(db, ["io"], worker_id="w2") is None


def test_dedupe_key_is_idempotent(db):
    first = jobs.enqueue(db, "noop", dedupe_key="asr:rec1:v1")
    second = jobs.enqueue(db, "noop", dedupe_key="asr:rec1:v1")
    assert first is not None
    assert second is None
    count = db.execute(text("SELECT count(*) FROM jobs")).scalar()
    assert count == 1


def test_priority_orders_claims(db):
    jobs.enqueue(db, "noop", priority=200, payload={"which": "low"})
    jobs.enqueue(db, "noop", priority=50, payload={"which": "high"})
    job = jobs.claim_next(db, ["io"], worker_id="w1")
    assert job is not None and job.payload["which"] == "high"


def test_fail_retries_with_backoff(db):
    jobs.enqueue(db, "noop")
    job = jobs.claim_next(db, ["io"], worker_id="w1")
    jobs.fail(db, job, "boom")

    db.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.error == "boom"
    assert job.locked_by is None
    assert aware(job.run_after) > datetime.now(UTC)  # backoff in the future

    # not claimable until run_after passes
    assert jobs.claim_next(db, ["io"], worker_id="w1") is None


def test_fail_exhausts_to_failed(db):
    jobs.enqueue(db, "noop", max_attempts=1)
    job = jobs.claim_next(db, ["io"], worker_id="w1")
    jobs.fail(db, job, "fatal")

    db.refresh(job)
    assert job.status == "failed"
    assert job.finished_at is not None


def test_stalled_jobs_are_requeued(db):
    jobs.enqueue(db, "noop")
    job = jobs.claim_next(db, ["io"], worker_id="w1")

    # simulate a dead worker: heartbeat far in the past
    job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=30)
    db.commit()

    n = jobs.requeue_stalled(db, stale_seconds=300)
    assert n == 1
    db.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert "stalled" in (job.error or "")
