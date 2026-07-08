# 0005 — Mirror raw bronze to Hugging Face (append-only)

**Status:** Accepted (2026-07)

## Context

USPTO is the true source of the raw zips, but reaching it requires a personal,
ID.me-verified API key, and the Open Data Portal rotates superseded files out
over time. That is reproduction friction for anyone else and a durability risk
for older snapshots.

## Decision

Mirror `bronze/` (annual + daily zips) to a **separate Hugging Face dataset
repo, append-only** — nothing is ever deleted from the mirror, even when local
`bronze/daily/` is pruned after an annual re-baseline.

## Consequences

- Any historical build is reproducible from public inputs, without USPTO
  credentials.
- Cloud runs restore bronze from the mirror at datacenter speed and only fetch
  genuinely new files from USPTO (`run --from-mirror`).
- The mirror grows monotonically (every daily and every annual edition ever
  fetched); this is intentional and cheap.
