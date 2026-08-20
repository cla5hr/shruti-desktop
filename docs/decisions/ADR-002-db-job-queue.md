# ADR-002: Hand-rolled DB job queue (no Redis/Celery)

**Status:** accepted (2026-08-17)

## Decision
Pipeline orchestration uses a `jobs` table in the app's own database (SQLite in the
desktop exe) with atomic claiming, heartbeats, exponential-backoff retries, unique
`dedupe_key` idempotency, and a stalled-job sweep. Implementation:
`packages/core/shruti_core/jobs.py` (~140 lines); contract specced by
`apps/worker/tests/test_queue.py`.

## Rationale
- Scale is a handful of job chains per day on one machine — orders of magnitude below
  where a broker earns its keep.
- The UI needs job states/progress in our schema anyway (pipeline tray, download
  progress); a broker would force mirroring state back into the database.
- Zero extra services to install: the exe stays a single process over one SQLite file.

## Revisit when
Multiple worker hosts with high contention, sub-second latency needs, or fan-out
patterns the explicit chaining can't express.
