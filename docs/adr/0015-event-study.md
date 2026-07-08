# 0015 — Event study: policy discontinuities vs. the composition surge

**Status:** Accepted

## Context

The project characterizes a post-2015 surge in coined marks filed around
marketplace brand-gating. Three policy events are candidate drivers, and a
reviewer will ask which, if any, moved the composition:

- **Amazon Brand Registry** relaunch (~2017-04) — a phased business rollout,
  not a legal date.
- **USPTO U.S.-counsel rule** (2019-08-03) — foreign-domiciled applicants
  must appoint a U.S.-licensed attorney.
- **Trademark Modernization Act** expungement/reexamination (2021-12-18).

The naive move — read composition breaks at these dates as policy effects —
fails on two counts. First, the largest break in the corpus is **2020**,
coincident with the pandemic e-commerce boom and *not* a policy date, so any
segment anchored on a policy date absorbs the pandemic surge. Second, the
U.S.-counsel rule *mechanically* changes attorney-of-record presence for
foreign applicants; treating that regulatory compliance shift as behavior
would be a category error (this is the "model it, don't discover it" break).

## Decision

Ship an event-study module (`gold/event_study.py`,
`trademark-radar event-study`) with a monthly composition series and a
**hybrid** analysis, keeping the descriptive and causal claims separate:

- **Descriptive interrupted time series** for the behavioral metrics
  (coined share, marketplace-class share, foreign-owner share, use-basis,
  goods-list length, log volume): segmented regression with a level and
  slope change at each of the three dates, month-of-year seasonality,
  volume-weighted (WLS), HAC/Newey-West errors. Breaks are *characterized,
  not causally attributed* — the write-up states plainly that the dominant
  break is the 2020 pandemic surge, which sits mid-segment and contaminates
  the post-2019 slopes, so the policy-date coefficients are descriptive.
- **Difference-in-differences** for the U.S.-counsel rule's mechanical
  effect on attorney-of-record presence: foreign (CN) applicants treated,
  domestic (US) applicants control, transition month dropped, volume-weighted,
  robust errors. This *is* causally identified. The anticipatory
  pre-deadline filing rush is reported alongside it.

The series is aggregate monthly statistics with no person-level data, so —
unlike operation_profile — it is publishable.

## Consequences

- The paper can state a clean, defended causal number for the one place it is
  identified (the rule's mechanical attorney-presence effect) while being
  honest that the behavioral surge is a 2020 phenomenon the policy dates
  bracket but do not explain.
- Live results (build through 2026-05): the rule's DiD effect on attorney
  presence is **+0.24** (SE 0.02, p < 0.001; CN 0.50→0.73 vs US 0.69→0.67),
  with a **4.4×** pre-deadline filing rush; Brand Registry marks a gradual
  coined-share slope onset (no level jump); the behavioral metrics turn
  significantly *down* around the TMA date (post-surge cooling that is not
  separable from pandemic mean-reversion and is not claimed as a TMA effect).
- The mechanical/behavioral split is enforced in code: attorney presence is
  excluded from the behavioral ITS and only enters the DiD.
- No 2020 "COVID control" is added (that would be an identification claim the
  data cannot support); the confound is disclosed, not modeled away.
