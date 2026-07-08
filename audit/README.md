# Grouping precision audit

Durable home for the **manual** part of the operation-grouping validation
(ADR 0014). The evidence being reviewed contains attorney names and stays
local under `data/gold/` (ADR 0008, git-ignored); only the name-free
verdicts land here, in the tracked tree.

## Workflow

1. Build the sample and the review app (reads the live build; local-only):

   ```
   trademark-radar validate-resolution
   ```

   This writes `data/gold/resolution_audit_v1/` with `audit_sample.json`
   (the evidence) and `review.html` (the review app).

2. Open `data/gold/resolution_audit_v1/review.html` in a browser (just open
   the file - no server). For each operation, read the name spellings,
   addresses, and applicants together and record a verdict:

   - **coherent** - the members plausibly share one filing operation.
   - **mixed** - the members are clearly unrelated filers (an over-merge).
   - **unclear**.

   One filer split across several operations is *not* an error here - ADR
   0011 accepts fragmentation as the safe failure, and the sensitivity
   analysis measures it separately. Only over-merge counts against
   precision. The notes box is local scratch (it stays in your browser and
   is never exported), so the committed `verdicts.json` is name-free by
   construction.

3. Click **Download verdicts** and save the file here as `verdicts.json`
   (the review app can re-import it later to resume). Commit it.

4. Score it:

   ```
   trademark-radar resolution-score audit/verdicts.json
   ```

   Precision = coherent / (coherent + mixed), overall and per size
   stratum, with Wilson 95% intervals. The full result is written next to
   the verdicts as `resolution_precision.json` (also name-free; commit it).

The sample is deterministic for a given build, so op ids are stable across
re-runs on the same build; a new monthly build reshuffles the sample, and
`resolution-score` warns if the verdicts were made against a different
build than the one currently on disk.
