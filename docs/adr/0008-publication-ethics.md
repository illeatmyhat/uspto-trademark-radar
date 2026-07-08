# 0008 — Neutral public dataset; person-level scoring stays local

**Status:** Accepted

## Context

The project publishes public-record trademark data and also runs analyses over
it. A clear rule is needed for what belongs in a public dataset versus what
stays a local analytical artifact.

A further hazard sharpens the rule: the USPTO's own sanctions record shows
that party-identifying metadata can be **fabricated or stolen** — false
domicile addresses, other people's signatures, and misappropriated U.S.-bar
credentials were part of the sanctioned conduct. So the attorney or party
named on a filing may be a *victim* of the scheme rather than its author. A
published score that attributes conduct to that name would risk defaming the
very people the scheme harmed. Attribution must therefore not be inferred
from forgeable filing metadata; culpability is something the Office
adjudicates (ADR 0010), not something this project asserts.

## Decision

The public dataset contains **source records and objective per-filing
measurements only.** Specifically:

- **Published:** the silver corpus (verbatim public records) and derived
  per-filing feature columns keyed by `serial_no` — e.g. a coined-mark
  linguistic score, class and basis flags, goods-list length. These describe
  the filing.
- **Not published:** analytical tables that aggregate to and rank identifiable
  parties (attorneys, correspondent addresses). These are local-only.

Aggregation from published per-filing features up to a party is left to the
consumer.

## Consequences

- The public dataset is neutral and factual; downstream labeling and
  thresholds are the consumer's choice, not the dataset's.
- The gold layer is two-tier by this rule: publishable per-filing feature
  tables vs. local party-level aggregates. The split is enforced in code — the
  local tables live under `data/gold/` (git-ignored) and are excluded from the
  publish path.
- Published prose describes aggregate register phenomena, not named parties.
