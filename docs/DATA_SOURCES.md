# Data sources

The source-of-truth facts about the USPTO products this pipeline consumes, and
the semantics that make the update logic correct. The choice to use *only*
these products is recorded in [ADR 0001](adr/0001-source-products.md).

## Products

- **`TRTYRAP`** — Trademark Full Text XML Data (No Images), Annual
  Applications. The complete backfile, April 1884 → December of the last
  completed year.
- **`TRTDXFAP`** — same, Daily Applications. The current calendar year, one
  file per day named `apcyymmdd.zip`.

Format: zips of XML conforming to the US Trademark Applications v2.0 DTD.

## Platform & access

- **Platform:** USPTO Open Data Portal (data.uspto.gov). History of drift —
  the Developer Hub was decommissioned June 2026; BDSS became ODP. Expect more
  reorganization over the years; the pipeline's preflight is built to detect
  it and fail with an actionable message.
- **Access:** a free API key tied to a USPTO.gov account with ID.me identity
  verification. Sent as the `x-api-key` header. Keys are personal and should
  be assumed dead after any long gap (see [`../RUNBOOK.md`](../RUNBOOK.md) §1).
- **Endpoints:**
  - manifest — `GET https://api.uspto.gov/api/v1/datasets/products/{shortName}`
  - file — `GET .../products/files/{shortName}/{fileName}` (302-redirects to a
    signed data.uspto.gov URL; follow redirects).
- **Rate limit:** 20 downloads of the *same* file per key per year —
  irrelevant to a clean pull, matters only for pathological retry loops.

### Observed platform facts (verified 2026-07)

- Manifests expose per-file `fileSize` but **no checksums**; integrity rests
  on size + zip CRC + a SHA-256 recorded at download.
- The signed download URL's S3 `ETag` is **not** a content MD5 (the bucket
  uses SSE-KMS); it is recorded only as an opaque content id.
- The zips do **not** bundle the DTD, contrary to older assumptions; element
  mappings are validated against live data instead.

## Key semantics (critical to correctness)

- A trademark case file is a **living record.** A daily file contains the
  **complete current record** of every case with activity that day — full
  field set plus cumulative event history. It is an **upsert stream, not
  deltas.**
- The annual is a **compacted snapshot** (the WAL-checkpoint / log-compaction
  analogy): the latest state of every case as of Dec 31, the entire backfile,
  intermediate versions collapsed.
- Therefore the same `serial_no` appears in many files; **latest wins**, keyed
  on `serial_no`, ordered by source-file transaction date.
- Because each annual is a full snapshot rather than a yearly increment, a
  multi-year operational gap is recovered by downloading only the *latest*
  annual. This drives the re-baseline strategy
  ([ADR 0006](adr/0006-annual-rebaseline.md)).

## Filename conventions

- Annual parts: `apc<START>-<END>-<PART>.zip` (8-digit dates); the **END**
  date is the snapshot's coverage cutoff — normative for the daily-replay
  boundary, not to be confused with the publication date.
- Dailies: `apcyymmdd.zip`; the 6-digit date is the transaction date.

Preflight treats a manifest of unparseable names as platform drift and stops.

## Code lookup tables

The six lookup CSVs packaged in `uspto_trademark_radar/lookups/` (and
published under `lookups/` in the silver dataset) are transcribed from
USPTO's own documentation; `lookups/_sources.json` pins each table's source
URL and retrieval date. Where they come from (verified 2026-07-08):

- **The TRTDXFAP product documentation is the canonical reference** for all
  code values in the XML: *Trademark Applications Daily XML Documentation*
  (v2.3 Word, Aug 2025) plus the standalone `Table1TrademarkStatusCodes` .doc
  under the product's files; an older v2.0 PDF (and a 508-accessible variant)
  lives on www.uspto.gov. Caveats: the non-508 v2.0 PDF truncates the
  party-type table; the 2023 status-code table adds codes and a
  Live/Dead/Indifferent column over the 2005 PDF edition.
- **Prosecution-history event codes** have the only machine-readable source:
  the Office of the Chief Economist's Trademark Case Files Dataset 2023
  release ships `event_description.csv` (905 (code, event_type) pairs,
  including the TMA expungement/reexamination proceeding codes, prefix `B*`
  plus `DIPX`/`DIPR`). It is derived from observed data, not a forward-looking
  registry.
- **Statement type codes are compositional**, not a flat list: a prefix
  family (`GS`, `DM`, `TR`, ...) plus positional suffix rules (e.g. `GS`
  positions 3-5 = prime class). `statement_types.csv` carries one row per
  family with its suffix rule.
- data.uspto.gov's file endpoint returns **HTTP 200 with an HTML SPA shell
  for nonexistent paths** — verify downloads by content (magic bytes), never
  by status code.
