# 0014 — Validating the operation grouping: audit sample + keying sensitivity

**Status:** Accepted

## Context

ADR 0011 chose non-transitive bounded keying for operation grouping on
design arguments (over-merge is the harmful failure; hub signals are
unreliable). Those arguments were untested empirically: no measurement said
how often a resolved operation actually mixes unrelated filers, or whether
the analysis's headline statistics depend on the specific keying rule
(name-first, hub caps of 50/25) rather than on the corpus. For publication,
both are the most likely methodological challenges.

## Decision

Ship a validation harness (`gold/resolution_audit.py`,
`trademark-radar validate-resolution`) with two artifacts, both local-only
(ADR 0008) and deterministic given the build (ADR 0013):

- **Precision audit sample.** A size-stratified sample of profiled
  operations (quotas of 45/35/20 over sizes 20–99 / 100–999 / 1000+, plus
  the 5 largest overall, ordered within strata by a content hash of the
  operation key, not an RNG), each laid out with its raw evidence:
  attorney-name spellings, correspondent addresses, applicant names and
  countries, with counts. A human reviews each operation for coherence in
  a self-contained local review app (`review.html`, evidence inlined) and
  exports a name-free `verdicts.json`; `resolution-score` turns that into a
  precision estimate with Wilson intervals.
  **Precision counts only over-merge as an error** — one filer fragmented
  across several operations is the accepted safe failure and is measured
  separately by the sensitivity analysis, not by the audit.
- **Keying sensitivity analysis.** The corpus is regrouped under
  alternative rules — stricter (25/12) and looser (100/50) hub caps,
  address-first priority, and the single-signal name-only / address-only
  rules a reviewer would propose — and the operation profiles are
  re-derived under each with the same weights. The report compares
  operation counts, coverage, maximum group size, score quantiles, the
  composition of the largest operations, and per-filing agreement with the
  shipped grouping (rank correlation of scores; Jaccard overlap of the
  top-decile mass).

- **Co-location diagnostic.** A ranked inspection list of non-hub
  correspondent addresses that host two or more distinct non-hub attorney
  names with peculiar-looking portfolios. It is a *diagnostic*, not part
  of the grouping: it merges no operations and asserts nothing. It exists
  because name-first keying deliberately discards the address signal, so a
  genuinely coordinated set of separately-named practices sharing an
  address would fragment silently; this surfaces such addresses for a
  human to inspect. Co-location is a weak, high-false-positive flag —
  shared offices are ordinary — so the output is explicitly framed as a
  reason to look, never a finding, and it never feeds back into the
  grouping (which would resurrect the ADR 0011 over-merge hazard).

Per-filing coined scores are reused from the persisted mark_features table
rather than refit, so the harness isolates the grouping choice — every
variant sees identical per-filing inputs.

## Consequences

- The paper can state a measured precision with a documented, reproducible
  sampling protocol instead of a design argument, and can show which
  headline statistics move (and how far) under alternative keying rules.
- Fragmentation becomes visible rather than silent: single-signal and
  address-first variants quantify how much coverage the shipped rule's
  name-first, hub-suppressed design buys.
- The audit evidence necessarily contains attorney names, so it lives
  under `data/gold/` with the other local-only artifacts and is never
  published; published methodology describes the protocol, not the
  members of any group.
- The manual verdicts are a human judgment call recorded via the review app;
  the harness makes the sample reproducible, not the judgment.
- The co-location diagnostic carries attorney names and is local-only
  (`data/gold/resolution_colocation.json`), never published. It informs a
  human's inspection and the paper's limitations discussion; it is never
  cited as evidence of coordination or misconduct about any party.
