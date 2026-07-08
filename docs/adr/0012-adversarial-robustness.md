# 0012 — Robustness to adversarial adaptation

**Status:** Accepted

## Context

The methodology is public — reproducibility demands it — so filers who read
it may change behavior to avoid the signals (Goodhart's law). The design has
to stay useful as behavior adapts, rather than measuring a target that erodes
the moment it is published.

## Decision

Anchor on what a filing operation cannot cheaply change, and make evasion
reveal itself:

- **Weight load-bearing signals over cosmetic ones.** The mark string can be
  renamed for free, but the studied business model cannot cheaply abandon
  one-disposable-owner-per-mark (owner churn) or the coined brandability the
  product needs. Grouping structure and owner churn carry weight the mark
  string cannot buy back.
- **Score coinedness per token and via two metrics** (ADR 0009), so padding a
  coined token with a common word does not hide it.
- **Group on the sticky attorney signal, non-transitively** (ADR 0011), so
  address rotation does not fragment an operation.
- **Record evasion traces as neutral facts.** Switching coined strings for
  real words raises confusing-similarity refusals (`final_refusal_in`);
  faking use raises use-statement abandonments (`abandoned_no_use_in`). Both
  are public prosecution-history facts, so filing "cleaner" marks to lower
  the mark-string signals surfaces elsewhere.
- **Re-validate monthly** against fresh adjudications (ADR 0010), so the
  signals track adaptation instead of freezing at one point in time.

## Consequences

- Published thresholds and weights are an operating point, not a frozen
  target: the *method* is open, the operating point can move.
- Findings are framed as signatures of *current* filing behavior, not
  timeless truths — an intervention that shifts behavior may appear as
  displacement rather than reduction, and that is stated.
- A fully-compliant, high-cost operation (many real attorneys, genuine use,
  real dictionary-word marks) is out of scope; this is documented as a
  limitation rather than papered over.
