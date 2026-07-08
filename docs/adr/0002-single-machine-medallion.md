# 0002 — Single-machine medallion, no orchestration framework

**Status:** Accepted

## Context

The corpus is large (~14M case files, ~250M events) but fits comfortably on
one machine's disk and in DuckDB's out-of-core execution. A workflow
framework (Dagster, Prefect, Airflow) would add operational surface, a
scheduler, and a dependency on infrastructure that must itself survive years
of dormancy.

## Decision

Run a **medallion** (bronze → silver → gold) on a **single machine** with no
heavyweight orchestration. Stage sequencing is plain code; scheduling is an
external cron (locally) or a cloud job runner.

## Consequences

- Minimal moving parts to keep alive across long gaps — a longevity goal.
- Parallelism where it pays comes from process fan-out over the job ledger
  ([ADR 0003](0003-diy-job-ledger.md)), not a distributed scheduler.
- Cloud execution is a stateless single run on local disk (a mounted network
  bucket is durable storage, not a viable working directory for the I/O-heavy
  parse stage); see [`../CLOUD.md`](../CLOUD.md).
- No framework means the pipeline owns its own resumability, which drives
  [ADR 0003](0003-diy-job-ledger.md).
