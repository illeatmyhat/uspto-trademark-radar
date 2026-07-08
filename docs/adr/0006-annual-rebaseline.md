# 0006 — Re-baseline from each annual snapshot

**Status:** Accepted

## Context

Each `TRTYRAP` annual is a full compacted snapshot of the entire backfile, not
a yearly increment ([DATA_SOURCES](../DATA_SOURCES.md)). Two strategies are
possible: (a) download one annual once and roll dailies forward forever, or
(b) rebuild from each new annual and replay only the dailies past its cutoff.

## Decision

**Re-baseline from each new annual.** When a new annual appears, rebuild silver
from it into a candidate, then replay only dailies with transaction date after
its coverage cutoff (Dec 31 of the covered year, not the publication date), in
date order.

## Consequences

- Self-healing: any drift accumulated during a year from a missed or malformed
  daily is wiped out by USPTO's authoritative annual compaction. Rolling
  dailies forever would let one bad daily diverge the dataset permanently with
  no detection.
- Multi-year-gap recovery is structurally free — one latest annual plus ≤12
  months of dailies fully rebuilds the corpus.
- Cost: re-downloading the full backfile (~15–20 GB) once a year. Accepted as
  cheap insurance for the correctness guarantee.
- Replay ordering is load-bearing: live silver may hold dailies newer than the
  annual, so the annual is a *baseline* that dailies are replayed on top of,
  never a blind overwrite.
