# 0017 — Tier 3: publish a neutral cluster-membership fact, not an identity

**Status:** Accepted

## Context

ADRs [0008](0008-publication-ethics.md) and [0011](0011-operation-resolution.md)
kept *all* operation-level (identity-derived) signals local: the grouping
itself, the `peculiarity_score`, and the co-location diagnostic. The rationale
holds: identifiers in the corpus are forgeable or stolen (so the named party
may be a victim), name-and-address grouping over-merges, and a published
party-ranking would defame without due process.

The detector lists are per-filing and publishable: Tier 1 is the USPTO's own
adjudicated sanctions, Tier 2 a transparent mark-composition composite. But
Tier 2 is a weak filing-level predictor of adjudicated conduct and, by design,
misses most of a high-volume operation's portfolio, because most of those
marks look ordinary. Measured on the label-complete pool (local spike):

- Base sanctioned-rate among labeled filings: **3.1%**.
- Tier 2-flagged: **4.8%** (~1.5x base).
- Membership in a large (>= 1000 marks), non-shared-service, non-transitive
  cluster: **10.6%** (~3.4x base), covering ~1.46M marks Tier 2 misses.

An owner-churn sweep sharpens the gate. Requiring `owner_turnover >= 0.6`
raises the lift to **19.6% (~6.3x base)** at ~937k marks while still covering a
tested high-volume operation in full (98%); `>= 0.8` (the first guess)
*over*-shot — it dropped both that operation and some genuinely high-sanction
ones, falling back to ~5x. Churn excludes near-zero-churn corporate portfolios
(one owner, many marks) without excluding real churn operations. So the
adopted gate is **size >= 1000 AND owner_turnover >= 0.6 AND not
is_shared_service**. At this size the resolution audit
([0014](0014-resolution-validation.md)) measured the grouping's over-merge
precision at ~1.0, so the fact is both strong and reliable.

The phenomenon is CN-concentrated (mega attorney operations at >= 90% Chinese
owners separate sanctions at ~8-9x base), but owner nationality is deliberately
**not** in the gate: the instrument screens filing behavior, not nationality
(consistent with the composite excluding `owner_country` from scoring). The CN
concentration is reported as characterization, not used as a criterion.

The identity-theft reality sharpens the framing rather than forbidding it: the
misappropriated credential means the *cluster* (an anomalous filing pattern)
is a real object, distinct from the person whose name it misuses.

## Decision

Introduce **Tier 3**, a publishable **per-filing structural fact**, under
strict guardrails. It is an inference tier, ranked below Tier 1.

1. **A fact, not an identity.** Publish only that a filing belongs to a cluster
   whose size is a large multiple of the median filer — a bucket or boolean,
   with the neutral size. Never publish the operation key, the attorney or
   correspondent name, or any table that ranks or names parties. The
   `peculiarity_score`, the co-location diagnostic, and the resolved-group
   evidence stay local (0008 unchanged for those).
2. **Accusation is the Office's, never ours.** "Operation", "identity theft",
   "bad-faith", and "sanctioned" attach only to Tier 1 (adjudicated). Tier 3's
   field is descriptive — "member of a high-volume, high-churn filing cluster"
   — an invitation to inspect, not a determination.
3. **Large clusters only.** Gate at `n_marks >= 1000`, where the grouping's
   over-merge precision is ~1.0, so the false-merge harm that kept grouping
   local does not occur at the published scale.
4. **Size *and* churn.** Require `owner_turnover >= 0.6` and exclude
   `is_shared_service`, so a corporate portfolio (many marks, one owner) or a
   multi-tenant filing service is not flagged. The 0.6 cutoff is the empirical
   sweet spot (see Context), not a round number.
5. **Non-transitive keying only.** No connected components, no bar-number or
   credential keying (the corpus carries none). Unchanged from 0011.
6. **Time-bounded identity.** Cluster membership is scoped to a filing's date;
   a cluster key may be split by timespan. If a filer regains control of a
   misappropriated credential, their later filings fall outside the
   compromised-period cluster and are not flagged — a victim is not permanently
   branded for a period they did not control. Because the published unit is the
   filing, a clean later period is simply unflagged; no schema change is needed
   to adopt this once a credential-recovery mechanism exists.
7. **Empirical gate.** Tier 3 ships only while cluster membership materially
   beats the base rate on adjudicated outcomes (measured ~5-6x). Each monthly
   rebuild re-checks the lift; if it collapses, the signal is scale, not
   conduct, and Tier 3 is pulled.

## Consequences

- Revises 0008/0011 narrowly: the neutral membership **fact** may be published
  under 1-7; everything that names, ranks, or scores a party stays local.
  Re-identification is acknowledged — `attorney_name` is already public, so
  cluster membership is re-derivable — which is exactly why the safeguard is
  *framing* (a verifiable fact plus adjudication-for-accusation), not secrecy.
- Tier 3 is an inference tier: ~97% of flagged marks are not adjudicated.
  Consumers must treat it as "inspect", link to the USPTO record, and support
  user allowlisting — like Tier 2, not Tier 1.
- The operation grouping needed to derive the fact runs in the pipeline, but
  only the per-filing membership fact (size bucket) is published; the grouping
  and its keys are not.
- If a future rebuild shows the sanction-lift gone, or a credential-recovery
  mechanism arrives, this decision is revisited in a new ADR, not edited.
