# USPTO Trademark Radar

A durable, resumable, single-machine pipeline that ingests the complete USPTO
trademark corpus (~12M+ application/registration records) from the Open Data
Portal, builds an analysis-ready relational Parquet dataset ("silver"), and
publishes it to Hugging Face.

- **Sources:** `TRTYRAP` (annual applications backfile, 1884→) and `TRTDXFAP`
  (daily applications, current year). Full-text XML, v2.0 DTD. Nothing else.
- **Architecture:** medallion (bronze zips → silver Parquet → gold analyses),
  DuckDB as the build/query engine, a DIY SQLite job ledger for
  resumability/idempotency, Hugging Face as the data host.
- **Docs:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (structure),
  [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) (the USPTO products),
  [`docs/silver-schema.md`](docs/silver-schema.md) (schema),
  [`docs/adr/`](docs/adr/) (decisions), and [`RUNBOOK.md`](RUNBOOK.md)
  (operations).

## Setup

```powershell
uv venv && uv pip install -e ".[dev]"
Copy-Item .env.example .env  # then fill in USPTO_API_KEY (+ HF_TOKEN for publish)
```

Secrets live in `.env` (git-ignored, loaded automatically by the CLI) or in
real environment variables, which take precedence.

## Usage

```powershell
trademark-radar preflight   # API reachable? products exist? filenames parse?
trademark-radar plan        # compare ODP manifests vs ledger; show what a run would do
trademark-radar run         # full update job: download -> parse -> build -> reconcile -> release -> prune
trademark-radar publish --repo you/uspto-trademarks
trademark-radar explore     # browse the data in a terminal SQL IDE (Harlequin)
trademark-radar sql "..."   # one-off query against the silver views
trademark-radar status      # ledger + watermark summary
```

Individual stages (`download`, `parse`, `build`, `reconcile`, `release`) are
also exposed for triage; `trademark-radar run` is just those in normative order
(see docs/ARCHITECTURE.md). Every stage is idempotent — recovery from any failure is
re-running the command.

Download and parse fan out across worker processes by default (auto-sized:
roughly all cores for parsing, a polite 8 connections for downloads), each
claiming jobs from the shared ledger — `--workers 1` forces serial operation,
and running the same stage from several terminals is also safe.

## Layout on disk (all git-ignored)

```
data/
  bronze/annual/    kept forever (canonical local cache of TRTYRAP)
  bronze/daily/     rolling window: only dailies after the latest annual cutoff
  silver_parts/     per-zip parsed Parquet, keyed by parser version (parse-once cache)
  builds/<id>/      candidate/live silver builds; LIVE.json points at live
ledger.sqlite       job ledger, download lineage, watermarks
```

## Data flow

1. **Download** new annual/daily zips (atomic `.part` → rename; ledger-tracked).
2. **Parse** each zip once into per-zip Parquet parts (streaming lxml;
   hard-stop on unknown DTD version).
3. **Build** a candidate: DuckDB latest-wins merge over (latest annual parts +
   post-cutoff daily parts), keyed on `serial_no`, ordered by source-file date.
4. **Reconcile** publish gates (uniqueness, referential integrity, date sanity,
   count tolerances); emit `profile/`.
5. **Release**: atomic pointer flip to the new build, then prune superseded
   daily zips (last, and only after gates pass).
6. **Publish** the live build to a Hugging Face dataset repo.
