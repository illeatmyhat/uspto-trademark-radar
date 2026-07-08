# 0004 — Hugging Face as the data host

**Status:** Accepted

## Context

The dataset (silver Parquet plus a raw mirror) is tens of GB — too large for
git/GitHub. It should be publicly queryable without consumers downloading it,
and versioned. Candidates considered: object storage (R2/S3), MotherDuck,
Hugging Face dataset repos.

## Decision

Host the data on **Hugging Face dataset repos**, queried by consumers via
DuckDB `hf://` paths.

## Consequences

- Each publish is a single commit, so versioning and rollback (revert the
  commit) are free.
- Consumers query in place — DuckDB `hf://`, the `datasets` library, or the
  Hub's viewer/SQL console — with no bulk download.
- Cloud jobs run inside Hugging Face's network, so the publish leg is an
  in-datacenter transfer rather than a residential upload.
- Part files are sized 256–512 MB and sorted by `serial_no` so row-group
  statistics make serial-range and date-range scans cheap.
