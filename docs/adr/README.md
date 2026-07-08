# Architecture Decision Records

Each ADR captures one significant decision: its context, the decision, and its
consequences. ADRs are immutable once accepted — a change in direction is a new
ADR that supersedes an old one, not an edit. This preserves *why* the project
is the way it is, rather than only its current state.

| # | Decision | Status |
|---|---|---|
| [0001](0001-source-products.md) | Use only the TRTYRAP + TRTDXFAP products | Accepted |
| [0002](0002-single-machine-medallion.md) | Single-machine medallion, no orchestration framework | Accepted |
| [0003](0003-diy-job-ledger.md) | DIY SQLite job ledger | Accepted |
| [0004](0004-hugging-face-data-host.md) | Hugging Face as the data host | Accepted |
| [0005](0005-bronze-mirror.md) | Mirror raw bronze to Hugging Face (append-only) | Accepted |
| [0006](0006-annual-rebaseline.md) | Re-baseline from each annual snapshot | Accepted |
| [0007](0007-latest-state-corrections.md) | Latest-state-only; no version archaeology | Accepted |
| [0008](0008-publication-ethics.md) | Neutral public dataset; private person-level scoring | Accepted |
| [0009](0009-coined-scoring.md) | Coined-mark scoring: two calibrated metrics, per token | Accepted |
| [0010](0010-outcome-validation.md) | Validate signals against USPTO-adjudicated outcomes | Accepted |
| [0011](0011-operation-resolution.md) | Operation resolution: non-transitive bounded keying | Accepted |
| [0012](0012-adversarial-robustness.md) | Robustness to adversarial adaptation | Accepted |
| [0013](0013-determinism.md) | Determinism as a correctness property | Accepted |
| [0014](0014-resolution-validation.md) | Validating the operation grouping: audit sample + keying sensitivity | Accepted |
| [0015](0015-event-study.md) | Event study: policy discontinuities vs. the composition surge | Accepted |
| [0016](0016-scoring-instrument.md) | Lead with the transparent composite; the fitted model validates it | Accepted |
| [0017](0017-tier3-cluster-membership.md) | Tier 3: publish a neutral cluster-membership fact, not an identity | Accepted |
