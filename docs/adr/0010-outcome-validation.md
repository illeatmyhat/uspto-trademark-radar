# 0010 — Validate signals against USPTO-adjudicated outcomes

**Status:** Accepted

## Context

The mark- and operation-level signals need to be *measurable*, not merely
plausible, and a publishable claim needs ground truth. The corpus already
records the outcomes the Office itself adjudicated.

## Decision

Build evaluation labels from USPTO's own recorded outcomes, and measure how
well the signals separate them:

- **Two outcome families, kept strictly separate and never pooled:**
  *sanctioned* filings (order-for-sanctions events / status 610) are a
  **filing-conduct** outcome; TMA expungement/reexamination *institution* is
  a **nonuse** outcome. A registration that fell out of use is a different
  phenomenon, and empirically the signals discriminate the two differently
  (coined-score AUC ≈ 0.55 against survivors for nonuse vs ≈ 0.78 for
  sanctions).
- **Survivors** (Section 8 continued-use affidavit accepted) are the
  negative class for the sanctions comparison.
- **Fit models only on label-complete cohorts** (filings 2015–2018): the
  survivor label needs a Section 8 window 5–6 years after registration, so
  recent filing years cannot contain negatives and a naive temporal split
  would measure label availability, not signal quality.
- **Validation only.** Labels come from enforcement and maintenance
  processes, not random audits; prevalence in the labeled pool is not
  population prevalence, and the sanctions set reflects a specific 2019–2021
  enforcement wave.

## Consequences

- `trademark-radar evaluate` reproduces the numbers, and re-measures against
  new adjudications as the monthly job ingests them.
- Signals are validated against *adjudicated* populations; they are not
  claimed as estimators of a population base rate.
- Where a model is trained, the target is an observable outcome (later
  sanctioned / instituted), not a latent label — so the enforcement
  selection bias is part of the stated estimand, not a hidden flaw.
