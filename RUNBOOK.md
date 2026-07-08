# RUNBOOK — USPTO Trademark Radar

For the operator (possibly you, years from now) restarting or repairing the
pipeline. Design context lives in `docs/ARCHITECTURE.md` and `docs/adr/`; the
silver schema in `docs/silver-schema.md`. The pipeline is built to survive multi-year dormancy:
one latest annual snapshot + ≤12 months of dailies fully rebuilds the dataset.

## 0. The monthly run

```powershell
$env:USPTO_API_KEY = "..."
trademark-radar run                 # add --repo user/uspto-trademarks to publish
```

Every stage is idempotent; after ANY failure the correct move is to fix the
cause and re-run the same command. `trademark-radar status` shows the job
ledger and watermarks; `trademark-radar retry [stage]` re-queues failed jobs.

The job also runs statelessly on cloud runners (better bandwidth than a
residential connection): see `docs/CLOUD.md` — GitHub Actions (free tier,
`.github/workflows/monthly-update.yml`) or Hugging Face Jobs (paid).
Both use `run --from-mirror` to restore bronze from the HF mirror first.

## 1. API key is dead (expected after any gap)

Keys are personal, tied to a USPTO.gov account with ID.me identity
verification — assume yours is gone after dormancy. Mint a fresh one:

1. Sign in / create an account at https://data.uspto.gov (ID.me flow).
2. Generate an API key in your profile; set `USPTO_API_KEY`.
3. `trademark-radar preflight` to confirm it works.

Note: the rate limit (20 downloads of the *same* file per key per year) only
bites pathological retry loops, never a clean pull.

## 2. Preflight reports PLATFORM DRIFT

The Open Data Portal reorganized (it has before: Developer Hub → BDSS → ODP
in June 2026). Distinguish the cases by the error message:

- **Product not found (404):** search data.uspto.gov by *title* — "Trademark
  Full Text XML Data (No Images)", Annual and Daily Applications variants —
  and update the short names in `uspto_trademark_radar/config.py`
  (`PRODUCT_ANNUAL`, `PRODUCT_DAILY`).
- **Manifest parses but no zips found:** the manifest JSON schema changed;
  update `_find_files()` in `odp.py` against the raw JSON.
- **Zips found but filenames don't parse:** naming convention changed; update
  `filenames.py` (annual `apcSTART-END-NN.zip`, daily `apcyymmdd.zip`).
- **Parser hard-stops on DTD version:** USPTO moved past v2.0. Diff the new
  DTD (bundled inside every zip) against the parser's element map in
  `parse_xml.py`, fix mappings, add the version to
  `config.SUPPORTED_DTD_VERSIONS`, bump `PARSER_VERSION` (invalidates the
  parse cache), and re-run. Never whitelist a version without diffing —
  silently mis-parsed silver is worse than a failed build.

## 3. Reconciliation failed

The candidate build stays on disk (`data/builds/build-…`), live is untouched,
dailies were NOT pruned. Triage by gate:

- **Duplicate serials / orphan child rows:** parser bug or a corrupt part —
  find the offending `file_name_source`, delete its dir under
  `data/silver_parts/`, re-run `parse` then `build`.
- **case_file shrank vs previous build:** input loss — usually missing annual
  parts. `trademark-radar plan` + check `bronze/annual/` holds every part of
  the latest snapshot.
- **Dates out of range:** inspect the offending column; extend the sentinel
  handling in `parse_xml._date` only if the source values are genuinely invalid.
- **Unknown file_name_source:** a zip landed in bronze outside the download
  stage; re-download through the pipeline or insert a `downloads` row.

After fixing: `trademark-radar build && trademark-radar reconcile && trademark-radar release`.

## 4. Bad publish on Hugging Face

Every publish is a single commit to the dataset repo. Roll back by reverting
the commit in the repo's history (Hub UI or
`huggingface_hub.HfApi.super_squash_history` alternatives — simplest is
`git revert` on a local clone of the dataset repo). Then fix locally and
re-publish.

## 5. January re-baseline

When a new annual snapshot appears (usually late winter for the prior year),
`run` handles it automatically: it downloads only the *latest* snapshot,
rebuilds silver from it, replays only the dailies newer than its coverage
cutoff (Dec 31 of the covered year — not the publication date), and prunes
superseded dailies last. This is also exactly the multi-year-gap recovery
path — a gap costs nothing beyond download time.

## 6. Disk layout & what is safe to delete

- `data/bronze/annual/` — keep forever (canonical local backbone; USPTO
  remains the true bronze).
- `data/bronze/daily/` — rolling window; pruned automatically after release.
- `data/silver_parts/` — cache; safe to delete anytime (costs a re-parse).
- `data/builds/` — immutable builds; `LIVE.json` points at the live one.
  Old builds are pruned to the last 2 by `release.prune_builds` (manual).
- `data/ledger.sqlite` — job history, download lineage, watermarks. Losing it
  loses lineage gates; rebuild by re-running downloads (they skip existing
  files but re-record lineage).

## 7. Bronze integrity model

Layered checks, all automatic (`integrity.py` has the full rationale):

| check | when | catches |
|---|---|---|
| Content-Length vs bytes written | during download | truncated transfer |
| USPTO manifest `fileSize` vs disk | download stage | wrong/partial object |
| S3 ETag recorded (opaque ID — SSE-KMS, so never a content MD5) | during download | silent server-side replacement, via later HEAD comparison |
| zip CRC sweep of every member | before job marked done | corrupt archive |
| SHA-256 recorded in ledger, re-verified | parse stage | at-rest rot/tampering, lineage proof |

USPTO publishes no official checksums (verified 2026-07-07), so authenticity
ultimately rests on TLS to `*.uspto.gov` plus the manifest size and ETag.
A corrupt download is deleted and its job marked failed — `trademark-radar
retry download` re-fetches it. A parse-time hash mismatch means bronze
changed on disk after download: delete the zip and re-run the download stage.
