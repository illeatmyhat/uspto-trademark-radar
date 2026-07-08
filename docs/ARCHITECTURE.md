# Architecture

How the pipeline is structured and why. For the *decisions* behind these
choices (with rationale and alternatives), see [`adr/`](adr/). For the source
data's shape and semantics, see [`DATA_SOURCES.md`](DATA_SOURCES.md). For the
schema, [`silver-schema.md`](silver-schema.md). For operations,
[`../RUNBOOK.md`](../RUNBOOK.md).

## Purpose

Ingest the complete USPTO trademark corpus (~14M application/registration
records), store it as an analysis-ready relational Parquet dataset, and
publish it — plus a raw source mirror — to Hugging Face for public querying.
The dataset is neutral; analytical scoring lives in a separate gold layer
(see [ADR 0008](adr/0008-publication-ethics.md)).

## Medallion layers

**Bronze** — raw source zips on disk. `bronze/annual/` is the canonical local
backbone (kept forever); `bronze/daily/` is a rolling window holding only
dailies after the latest integrated annual's coverage cutoff. USPTO is the
true bronze; local zips are a cache. Bronze is additionally mirrored to a
Hugging Face dataset repo (append-only) so any build is reproducible without
USPTO credentials ([ADR 0005](adr/0005-bronze-mirror.md)).

**Silver** — relational Parquet, one row per case file's latest known state,
per [`silver-schema.md`](silver-schema.md). Parse-once, query-many;
OCE-aligned naming so existing research code ports directly. `serial_no`
(zero-padded string) is the universal key.

**Gold** — derived analyses as versioned tables (`mark_features_v1`,
`operation_profile_v1`, …). A methodology revision lands as a new version
beside the old one, never mutating it, so analyses citing an old version stay
reproducible. Two tiers by publishability: objective per-filing feature tables
(publishable) vs. identifiable-party scoring (private). See
[ADR 0008](adr/0008-publication-ethics.md).

## Components

- **Query/build engine:** DuckDB over local Parquet; consumers query via
  `hf://` paths ([ADR 0004](adr/0004-hugging-face-data-host.md)).
- **Job ledger:** a DIY SQLite ledger with atomic leased claiming, driving
  every stage; also the lineage store ([ADR 0003](adr/0003-diy-job-ledger.md)).
  Every silver row carries `file_dt_source`/`file_name_source` traceable to a
  ledger download record.
- **Publishing:** Hugging Face dataset repos; each publish is one commit, so
  versioning and rollback are free.

## The update job (normative ordering)

Monthly cadence; tolerates multi-year gaps. Ordering is an invariant — the
prune step in particular is gated on reconciliation and runs last.

1. **Preflight** — manifest reachable, products exist, filenames parse; fail
   with a message distinguishing platform drift from transient errors.
2. **Detect** — compare the annual manifest against the ledger's last
   integrated annual.
3. **Download** the latest annual if new (only the latest, even after a gap)
   plus dailies past its cutoff.
4. **Rebuild silver from the annual into a CANDIDATE dir** (never in place).
5. **Replay dailies** with transaction date after the annual's coverage
   cutoff (Dec 31 of the covered year, *not* the publication date), in date
   order: per serial, upsert `case_file` and delete-and-rewrite all child
   rows. Ordering matters — live silver may hold dailies newer than the
   annual, which a naive annual upsert would regress
   ([ADR 0006](adr/0006-annual-rebaseline.md)).
6. **Reconcile** (gates in [`silver-schema.md`](silver-schema.md) §5). Failure
   → no swap, no prune, no publish.
7. **Atomic swap** candidate → live (versioned dir + pointer flip); in-flight
   readers keep the old build.
8. **Prune** `bronze/daily/` at or before the cutoff — last, gated on
   reconcile.
9. **Advance watermark**; emit `data_current_through` (= last daily applied).
10. **Publish** to Hugging Face.

No new annual → steps 5–10 only (a normal monthly daily sync).

## Idempotency & recovery

Every stage is idempotent, so recovery from any failure is re-running the
command:
- downloads write `.part` then atomically rename;
- parse outputs are keyed by (input file, parser version) — a parser-version
  bump invalidates the cache;
- builds are candidate dirs merged latest-wins, never in-place mutation;
- the ledger reclaims dead workers via lease/heartbeat.

Multi-year-gap recovery is structurally free: the latest annual is a full
snapshot, so a successor downloads one annual plus ≤12 months of post-cutoff
dailies. The residual risks are platform drift and DTD drift, both handled by
preflight and a parser hard-stop on unknown DTD versions.

## Environment

Python with `requests`, `duckdb`, `pyarrow`, `lxml`, `huggingface_hub`, `typer`
(large files are streamed, never DOM-loaded). Secrets (`USPTO_API_KEY`,
`HF_TOKEN`) come from the environment / `.env` and are never committed. Code
lives in git/GitHub; data never does (size; Hugging Face is the data host).
Cloud execution runs on local container disk — see [`CLOUD.md`](CLOUD.md).
