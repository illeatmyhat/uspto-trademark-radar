# 0013 — Determinism as a correctness property

**Status:** Accepted

## Context

The pipeline publishes derived scores and re-runs monthly. If a rebuild over
identical inputs produces different outputs, the published numbers are not
reproducible and validation drifts for reasons unrelated to the data. Several
derivations were silently non-deterministic — most consequentially the coined
scorer's fit, whose calibration bounds were observed to drift between
identical rebuilds.

## Decision

Every published derivation must be **deterministic given its inputs**:

- Replace DuckDB reservoir `USING SAMPLE` — not reproducible under
  multithreaded scans — with **hash-ordered sampling**
  (`ORDER BY hash(serial_no), serial_no LIMIT n`) for the scorer fit.
- Give every output-ordering and "pick one row" query a **total-order
  tiebreak**: the operation ranking, and the owner-selection subqueries that
  feed the published `owner_country`.
- Make derived keys **order-independent**: the operation key depends on the
  row set, not scan order (ADR 0011).
- **Accept and document** the one benign residual — the case_file
  duplicate-serial dedup, which is non-deterministic only among
  near-identical rows from the same source file.

## Consequences

- Published coined scores and owner attributes reproduce across rebuilds;
  the scorer calibration is byte-identical across independent fits (verified).
- Validation numbers change only when the data changes, not from run-to-run
  noise.
- One caveat: DuckDB's `hash()` is stable only within a major version, so a
  DuckDB upgrade can change the fit sample. Recorded here as the single
  cross-version reproducibility dependency.
