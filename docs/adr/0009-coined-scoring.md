# 0009 — Coined-mark scoring: two calibrated metrics, scored per token

**Status:** Accepted

## Context

"Coined" wordmarks — random-looking, non-dictionary strings — are a central
mark-composition signal. The score must be reproducible, defensible under
peer review, and robust to gaming once the methodology is public. Running
human-annotation trials to validate the construct is out of scope.

## Decision

Operationalize coinedness with **two independent metrics**, scored **per
token**:

- A character **n-gram improbability** model fit on a pre-2015 baseline of
  ordinary trademark wording (self-contained, corpus-specific), calibrated
  to `[0,1]` against the fitting distribution.
- Token **zipf-frequency** from the external `wordfreq` package — an
  established, citable lexicon that anchors the construct without annotation
  trials.

Both are computed **over the mark's whitespace tokens** (max improbability /
min frequency), not only whole-string, so a coined token padded with an
ordinary word (`ZORVEX GEAR`) is not diluted.

## Consequences

- The two metrics are only weakly correlated (r ≈ 0.23), so they carry
  complementary evidence; both are reported.
- Per-token variants trade a little clean-data accuracy for evasion
  resistance. The whole-string / max-frequency variants are retained and
  reported alongside for comparison.
- No human-annotation dependency — the external lexicon is the construct
  anchor and the enforcement labels (ADR 0010) are the validation.
- The pre-2015 fit is a fixed ordinary-English baseline that predates the
  studied post-2015 shift; the fit sample is deterministic (ADR 0013).
