# Related work and positioning

This project characterizes the post-2015 surge in coined marks filed around
marketplace brand-gating, and validates its signals against the outcomes the
USPTO itself adjudicated. Five strands of prior work bear on it. For each we
note the contribution and the methodological gap this project fills; the
closing section states the position directly.

> Citation details below (authors, years, venues) should be verified against
> the linked sources before use in a manuscript.

## 1. Trademark depletion and congestion

Beebe and Fromer's *Are We Running Out of Trademarks? An Empirical Study of
Trademark Depletion and Congestion* (Harvard Law Review, 2018) is the
foundational empirical account of register crowding: analyzing the full run
of applications since 1985, they find the supply of competitively effective
word marks is exhaustible and already depleted — a large share of common
English words and surnames is claimed or confusingly similar to a live mark.

**Relation.** Depletion is the *demand-side* backdrop for this project's
subject: as ordinary vocabulary is exhausted, coined strings become the
economically available option, which is exactly the linguistic signature we
score. This project does not measure depletion; it characterizes one
downstream filing pattern and asks whether its composition tracks adjudicated
outcomes.

## 2. Specimen studies and the non-use phenomenon

The nearest prior work is Beebe and Fromer's *Fake Trademark Specimens: An
Empirical Analysis* (Columbia Law Review). On a random sample of **365**
Chinese-origin use-based applications in **Class 25 (apparel), filed in
2017**, they hand-code twelve indicators of specimen irregularity and report
that a majority exhibited them, while only a small fraction drew examiner
refusals — a large detection gap. They estimate a substantial share of all
2017 use-based applications came from this population and argue it compounds
the depletion problem.

**Relation and gap.** This is the closest antecedent, and the contrast is the
core of our positioning:

- **Scope.** Their study is one class, one year, a 365-application manual
  sample; ours scores the entire Open Data Portal corpus (14M+ filings, all
  classes, 2013 onward) through a reproducible pipeline.
- **Labels.** Their indicators are researcher-coded judgments of specimen
  irregularity; ours are the *Office's own* adjudicated outcomes — the
  sanction events and status codes the USPTO entered — used as external
  ground truth (see ADR 0010). We validate signals *against* adjudications
  rather than defining the target ourselves.
- **Object.** They examine specimens; we score the mark string (a calibrated,
  per-token coined signal), class composition, goods-list structure, and
  filing basis, and keep specimen-adjacent outcomes as reported signals, not
  inputs.

## 3. The non-market driver: subsidies and the USPTO OCE report

The USPTO Office of the Chief Economist report *Trademarks and Patents in
China: The Impact of Non-Market Factors on Filing Trends and IP Systems*
(2021) documents the mechanism behind the surge: numerous sub-national
subsidy measures in China that can exceed the cost of a U.S. filing, creating
an incentive to file without a bona fide intent to use. It reports a very
large increase in U.S. filings from China after the 2015 electronic-filing
fee reduction, with a single city (Shenzhen) accounting for a large share of
Chinese-origin applications at the peak.

**Relation.** This is the economic explanation for the composition shift our
event study measures (ADR 0015). The OCE report is a descriptive/policy
account; it does not ship an open, reproducible instrument or a validation
against adjudicated outcomes. Our foreign-owner-share series and the
difference-in-differences around the 2019 U.S.-counsel rule quantify, on the
public corpus, phenomena the report describes.

## 4. Enforcement and adjudication as ground truth

The USPTO's sanctions proceedings — the precedential orders terminating tens
of thousands of applications and registrations tied to bad-faith and non-use
filing conduct, and disciplinary actions against practitioners — are the
enforcement record of the same period. These adjudicated outcomes are public.

**Relation.** Where the literature above *characterizes* the population, we
*use the Office's adjudications as labels*: the sanctioned cohort (sanction
events and the corresponding status) is the positive class our signals are
measured against, kept strictly separate from the Trademark Modernization
Act's nonuse (expungement/reexamination) outcomes, which are a different
phenomenon (ADR 0010). Treating the enforcement record as ground truth — not
our own coding — is the methodological move that distinguishes this work from
the manual-coding studies.

## 5. Computational and econometric approaches

A separate strand applies computation to trademarks, but to adjacent
problems: applicant-level "squatter scores" and empirical squatter
identification in registers (e.g., theory-and-evidence studies in other
jurisdictions, and WIPO Economic Research working papers); machine-learning
classification of mark *distinctiveness* (the *Automating Abercrombie* line,
Journal of Empirical Legal Studies, 2024); and e-commerce infringement /
similar-mark retrieval datasets (e.g., the TMID dataset, arXiv:2312.05103).
The canonical research corpus is the USPTO Trademark Case Files Dataset
(Graham, Marco, and Miller, Journal of Economics & Management Strategy,
2018), a periodically released static extract.

**Relation and gap.**

- The squatter-score work scores *applicants* to rank likely squatters; we
  deliberately avoid a person-level published score, resolve filing
  operations *non-transitively* to bound over-merge, and keep any
  operation-level scoring local and unpublished (ADR 0008, ADR 0011), with a
  measured grouping precision (ADR 0014). The ethics-forward grouping is
  itself a contribution the squatter-score literature does not address.
- The distinctiveness / retrieval work targets classification and image
  similarity, not the filing-conduct composition surge or validation against
  adjudicated outcomes.
- Relative to the static Case Files Dataset, this project rebuilds an
  analysis-ready relational corpus from the live Open Data Portal on a
  durable, resumable pipeline (current through 2026), publishes it openly,
  and layers the coined-scoring, validation, event-study, and
  grouping-validation instruments on top.

## Positioning, in one paragraph

Prior work is either legal-empirical but **narrow and manually coded**
(Beebe and Fromer's specimen study: one class, one year, 365 hand-coded
applications), **agency description without an open instrument** (the OCE
report), **case-by-case enforcement** (the sanctions orders), or
**computational but aimed at a different question** (distinctiveness,
image retrieval, applicant squatter ranking in other registers). This
project's contribution is the combination the literature lacks: a
**reproducible, corpus-wide, open** characterization of the coined-mark
surge; a **transparent per-filing instrument validated against the USPTO's
own adjudicated outcomes** (~0.89 AUC, with a fitted model confirming
fitting adds little — ADR 0016); an **event-study identification** that
separates the mechanical effect of the 2019 U.S.-counsel rule from the
2020 pandemic surge (ADR 0015); and a **defamation-aware entity resolution**
with measured precision that keeps person-level inference out of the
published record (ADR 0011, ADR 0014).

## References

- Beebe, B. & Fromer, J. C. *Are We Running Out of Trademarks? An Empirical
  Study of Trademark Depletion and Congestion.* Harvard Law Review 131:945
  (2018). https://harvardlawreview.org/print/vol-131/are-we-running-out-of-trademarks/
  · SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3121030
- Beebe, B. & Fromer, J. C. *Fake Trademark Specimens: An Empirical
  Analysis.* Columbia Law Review.
  https://columbialawreview.org/content/fake-trademark-specimens-an-empirical-analysis/
- USPTO Office of the Chief Economist. *Trademarks and Patents in China: The
  Impact of Non-Market Factors on Filing Trends and IP Systems* (2021).
  https://www.uspto.gov/sites/default/files/documents/USPTO-TrademarkPatentsInChina.pdf
- USPTO. Sanctions proceedings and terminations of bad-faith filings
  (precedential sanctions orders and practitioner discipline; the adjudicated
  record used here as ground-truth labels).
  https://www.uspto.gov/about-us/news-updates/uspto-has-terminated-more-52000-fraudulently-filed-trademark-applications-and
- Graham, S. J. H., Marco, A. C. & Miller, R. *The USPTO Trademark Case Files
  Dataset: Descriptions, Lessons, and Insights.* Journal of Economics &
  Management Strategy (2018).
  https://www.uspto.gov/ip-policy/economic-research/research-datasets/trademark-case-files-dataset
- *Automating Abercrombie: Machine-Learning Trademark Distinctiveness.*
  Journal of Empirical Legal Studies (2024).
- *TMID: A Comprehensive Real-world Dataset for Trademark Infringement
  Detection in E-Commerce.* arXiv:2312.05103. https://arxiv.org/abs/2312.05103
- *Trademark Squatters: Theory and Evidence from Chile* and related WIPO
  Economic Research working papers.
  https://www.sciencedirect.com/science/article/abs/pii/S0167718716301011
- WIPO. *World Intellectual Property Indicators* (2024/2025).
  https://www.wipo.int/web-publications/world-intellectual-property-indicators-2024-highlights/en/trademarks-highlights.html
