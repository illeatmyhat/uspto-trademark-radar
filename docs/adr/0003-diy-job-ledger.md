# 0003 — DIY SQLite job ledger

**Status:** Accepted

## Context

Without an orchestration framework ([ADR 0002](0002-single-machine-medallion.md)),
the pipeline still needs resumable, idempotent, optionally-parallel stages, and
a lineage record tying every published row back to a source file.

## Decision

Own this layer directly: a small **SQLite job ledger**. (Postgres is a drop-in
if multi-process claiming ever needs `FOR UPDATE SKIP LOCKED`; SQLite suffices
today.)

- `jobs` table: `(work_unit, stage, status, attempt_count, timestamps, error)`,
  states `pending → claimed → running → done | failed`.
- Unit of work = one zip per stage (download X / parse X).
- **Atomic claiming** via `UPDATE … WHERE status='pending' RETURNING` in a
  transaction, plus a lease/heartbeat so a dead worker's jobs become
  reclaimable.
- **Idempotency everywhere:** downloads `.part`+rename; parse outputs keyed by
  (input file, parser version); builds are candidate merges. Recovery =
  re-run anything not `done`.
- A `watermark` table tracks the daily tail (`last_daily_integrated`).
- The ledger **doubles as lineage**: every silver row carries
  `file_dt_source`/`file_name_source`, and a reconcile gate asserts each traces
  to a ledger download record.

## Consequences

- Full resumability and safe parallel fan-out (multiple worker processes claim
  distinct jobs) with no external dependency.
- The owner understands and controls every line of the job system — an
  explicit preference and a longevity hedge.
- Lineage is a byproduct of the same table, not a separate system.
