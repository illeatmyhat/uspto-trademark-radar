# 0016 — Lead with the transparent composite; the fitted model validates it

**Status:** Accepted

## Context

Two instruments score the coined-mark signal: a **transparent composite** —
a fixed, a-priori weighting of the per-filing features (rarest-token zipf,
n-gram coinedness, marketplace class, goods-list length, use-basis, single
token, real-translation anti-signal) — and a **fitted logistic model** over
the same features, trained on the label-complete 2015–2018 cohorts against
USPTO-adjudicated outcomes. The paper needs one of them as its headline
instrument. The naive choice is the fitted model, because it scores highest.

Measured head-to-head on the same standardized holdout
(`trademark-radar evaluate`, `scorer_comparison`):

- Fitted logistic: test AUC **0.906**, well-calibrated (Brier 0.009;
  decile predicted vs. actual track closely).
- Transparent fixed weighting: test AUC **0.889** — chosen a priori, not
  tuned.
- Equal-weight composite: **0.872**.
- Best single signal (rarest-token zipf): 0.79.

Fitting buys ~1.5 AUC points over a fixed transparent weighting; nearly all
the lift over a single signal comes from *combining* features, not from
*fitting* them. The fitted coefficients are only moderately stable across
sub-cohorts (correlation 0.72; cross-cohort AUC falls to 0.82–0.88), a
standard reviewer attack surface for a fit instrument.

## Decision

The paper **leads with the transparent composite** as the primary,
deployed, publishable instrument, and uses the **fitted logistic model as
its validation**, not as the headline. Concretely:

- The transparent weighting (`evaluation.TRANSPARENT_WEIGHTS`) is fixed,
  documented, and derived from the coined-mark thesis rather than tuned, so
  it is immune to the "you fit the weights to get the number" critique and
  is reproducible without a training step.
- The fitted model does two jobs: it demonstrates that fitting leaves almost
  nothing on the table (0.89 ≈ 0.91), and it supplies **calibrated
  probabilities** where a probability (not just a rank) is needed.
- `evaluate` reports both AUCs, the fitted model's Brier and calibration,
  and the cross-cohort coefficient stability, so the comparison is a
  reproducible artifact, not a one-off claim.

## Consequences

- The paper's central quantitative instrument is transparent, fixed, and
  filing-level (hence publishable), which aligns the headline instrument
  with the publishable `mark_features` layer rather than the local-only
  operation score.
- The operation-level `peculiarity_score` (ADR 0011, local-only) remains a
  transparent *descriptive* ranking aid for inspection — it is not the
  paper's validated instrument and does not need to be.
- Reporting both instruments and their agreement is itself the robustness
  result; the fitted model's calibration and the transparent weighting's
  stability are complementary strengths, presented as such.
- If a future corpus makes fitting worth its cost (a materially larger AUC
  gap), this decision is revisited in a new ADR, not edited.
