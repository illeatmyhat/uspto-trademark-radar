# Reproducible analysis: filing-pattern concentration in the trademark corpus

This is a transparent, runnable methodology for studying **who files what** in
the post-2015 US trademark surge — filing concentration, mark composition,
ownership geography, and goods-description patterns. Everything below runs
directly against the published dataset with only DuckDB; no download, no
credentials. Every query returns statistics computed from public records; the
closing note covers how to read them.

The [Trademark Modernization Act of 2020](https://www.uspto.gov/trademarks/laws/2020-modernizing-law)
and USPTO's Office of the Chief Economist have both documented a post-2015
surge of coined marks filed around marketplace brand-gating (e.g. Amazon Brand
Registry). This methodology characterizes that phenomenon quantitatively.

## Setup (three lines)

```sql
-- DuckDB CLI or any DuckDB client. Data streams from Hugging Face.
INSTALL httpfs; LOAD httpfs;
CREATE VIEW case_file    AS SELECT * FROM 'hf://datasets/illeatmyhat/uspto-trademarks/data/case_file/*.parquet';
CREATE VIEW owner        AS SELECT * FROM 'hf://datasets/illeatmyhat/uspto-trademarks/data/owner/*.parquet';
CREATE VIEW intl_class   AS SELECT * FROM 'hf://datasets/illeatmyhat/uspto-trademarks/data/intl_class/*.parquet';
CREATE VIEW statement    AS SELECT * FROM 'hf://datasets/illeatmyhat/uspto-trademarks/data/statement/*.parquet';
CREATE VIEW correspondent AS SELECT * FROM 'hf://datasets/illeatmyhat/uspto-trademarks/data/correspondent/*.parquet';
```

## The building blocks

Each query below is an independent, ordinary descriptive statistic. Composed,
they characterize a filing portfolio.

**1. The surge, by owner geography and marketplace-gated class.** Classes
9/11/18/20/21/25/28 are the Amazon-heavy consumer categories.

```sql
SELECT year(cf.filing_dt) AS yr, o.own_addr_country_cd AS country,
       count(DISTINCT cf.serial_no) AS marks
FROM case_file cf JOIN owner o USING (serial_no)
JOIN intl_class ic USING (serial_no)
WHERE cf.filing_dt >= DATE '2015-01-01'
  AND ic.intl_class_cd IN ('009','011','018','020','021','025','028')
GROUP BY 1,2 ORDER BY yr, marks DESC;
```

**2. Coined single-token marks.** Standard-character (drawing code `4`),
one word, 4–9 letters — the linguistic signature of a coined brand.

```sql
SELECT serial_no, mark_id_char FROM case_file
WHERE mark_draw_cd = '4' AND filing_dt >= DATE '2015-01-01'
  AND strpos(trim(mark_id_char), ' ') = 0
  AND length(mark_id_char) BETWEEN 4 AND 9;
```

**3. Filer concentration.** Which attorneys of record account for the most
filings, and what does their portfolio look like? (The `attorney_name` field
is public record on every application.)

```sql
SELECT cf.attorney_name,
       count(*) AS marks,
       avg((o.own_addr_country_cd = 'CN')::INT)          AS pct_cn,
       avg((cf.use_af_in)::INT)                          AS pct_use_basis,
       avg((cf.mark_draw_cd='4'
            AND strpos(trim(cf.mark_id_char),' ')=0
            AND length(cf.mark_id_char) BETWEEN 4 AND 9)::INT) AS pct_coined
FROM case_file cf JOIN owner o USING (serial_no)
WHERE cf.filing_dt >= DATE '2015-01-01' AND cf.attorney_name IS NOT NULL
GROUP BY 1 HAVING count(*) >= 500
ORDER BY marks DESC;
```

**4. Client relationship (the legitimate-practice discriminator).** A law
practice files many marks for a few repeat clients; a high-volume filing
operation files one mark each for many distinct owners. "Marks per distinct
owner" separates them — high values indicate an ongoing practice, values near
1 indicate owner churn.

```sql
WITH per_owner AS (
  SELECT cf.attorney_name, upper(trim(o.own_name)) AS owner, count(*) c
  FROM case_file cf JOIN owner o USING (serial_no)
  WHERE cf.filing_dt >= DATE '2015-01-01'
  GROUP BY 1,2)
SELECT attorney_name, sum(c) AS marks, count(*) AS distinct_owners,
       sum(c)*1.0/count(*) AS marks_per_owner
FROM per_owner GROUP BY 1 HAVING sum(c) >= 500
ORDER BY marks_per_owner ASC;   -- lowest = highest owner churn
```

**5. Goods-list length.** Count the semicolon-separated items in the
goods/services identification. Coherent filings list a few related goods;
padded filings list many unrelated ones.

```sql
SELECT serial_no,
       length(statement_text) - length(replace(statement_text,';','')) + 1 AS n_items
FROM statement WHERE statement_type_cd LIKE 'GS%'
ORDER BY n_items DESC;
```

**6. Operation-level aggregation.** Filings are grouped into operations by
their most reliable *non-hub* signal — the canonical attorney name first
(a licensed attorney of record is sticky and hard to rotate), the
correspondent address second. The query below sketches address-only
grouping; the repository's module (next section) implements the full
name-first, hub-suppressed, non-transitive keying.

```sql
SELECT regexp_replace(upper(concat_ws(' ', cor_addr_2, cor_addr_3, cor_addr_4)),
                      '[^A-Z0-9]', '', 'g') AS operation,
       count(*) AS marks,
       count(DISTINCT c.serial_no) AS n_filings
FROM correspondent c GROUP BY 1 ORDER BY marks DESC;
```

## Putting it together

The repository's `uspto_trademark_radar.gold.operation_profile` module composes
these signals into a single ranked table (`trademark-radar gold`), scoring
each operation by a transparent weighted blend of owner-churn, coined-mark
rate, marketplace-class rate, use-basis rate, and goods-list length, and also
reporting prosecution-outcome rates (final refusals, use-statement
abandonments). Clone the repo, point it at the data, and run it — the ranking
is fully reproducible from these public inputs.

## Robustness to evasion

Because the methodology is public, a filer could try to change behavior to
avoid the signals. The design leans on the parts of the behavior that are
expensive to change:

- **Coinedness is scored per token**, so padding a coined string with an
  ordinary word (`ZORVEX` → `ZORVEX GEAR`) does not hide it, and both a
  corpus-fit n-gram model and an external dictionary (`wordfreq`) must agree.
- **Operations are grouped name-first, non-transitively.** Rotating the
  correspondent address does not fragment an operation (its attorney of
  record still links the filings), while common names and shared mailboxes
  are excluded as unreliable so unrelated filers are never merged.
- **Evasion leaves its own traces.** Switching coined strings for real words
  raises confusing-similarity refusals (`final_refusal_in`); faking use
  raises use-statement abandonments (`abandoned_no_use_in`). Both are
  reported, so lowering the mark-string signals by filing "cleaner" marks
  shows up elsewhere.
- **Labels refresh monthly.** Validation tracks new USPTO adjudications as
  they arrive, so the signals are re-measured against current enforcement
  rather than frozen.

### Validating the grouping itself

The grouping rule is measured, not assumed
(`trademark-radar validate-resolution`;
`uspto_trademark_radar.gold.resolution_audit`):

- **Manual precision audit.** A deterministic, size-stratified sample of
  resolved operations (including the largest ones, where a wrong merge
  matters most) is laid out with each group's raw evidence — name
  spellings, correspondent addresses, applicant names — for human review.
  Only over-merge (members from clearly unrelated filers) counts against
  precision; splitting one filer across groups is the designed-in safe
  failure. The evidence contains attorney names, so the sample stays
  local and unpublished; the protocol here is what is public.
- **Keying sensitivity.** The corpus is regrouped under alternative rules
  (stricter/looser hub caps, address-first, name-only, address-only) and
  every operation-level statistic is re-derived under each, holding the
  per-filing inputs fixed. Reported side by side: operation counts,
  coverage, largest group size, score quantiles, the composition of the
  top operations, and per-filing agreement with the shipped grouping.
  Results that survive every keying rule are properties of the corpus;
  anything that moves is flagged as grouping-dependent.

The honest limits: an operation willing to bear real cost — many licensed
attorneys, genuine use, real dictionary marks — can still evade, and the
sanctions labels reflect a specific 2019–2021 enforcement wave, so a
high-volume operation that files genuine specimens and ordinary marks is out
of scope. These signals characterize filing behavior; they are not a
determination of misconduct about any filer.

## Validation against USPTO-adjudicated outcomes

The corpus itself records outcomes the Office adjudicated, which makes the
signals above measurable rather than merely plausible. The repository's
`trademark-radar evaluate` command reproduces all numbers in this section
(`uspto_trademark_radar.gold.evaluation`; requires `uv sync --group analysis`).

**Outcome labels** (from prosecution-history events and status codes — see
`lookups/`):

- `sanctioned` — order-for-sanctions / terminated-after-sanctions events or
  status 610 (n = 79,311; concentrated in 2019–2021 filings). A
  filing-conduct outcome.
- `tma_instituted` — TMA expungement/reexamination proceeding instituted
  (n = 431, post-Dec-2021). A **nonuse** outcome, deliberately never pooled
  with the filing-conduct labels: a mark that fell out of use is a different
  phenomenon from the conduct cases, and empirically the two populations
  look nothing alike on mark-composition signals (coined-score AUC ≈ 0.55
  against survivors, i.e. barely above chance, vs 0.78 for the sanctions
  comparison).
- `survivor` — registration whose §8 continued-use affidavit was accepted
  (n = 2.48M). Negative class for the sanctions comparison.

**Headline measurements** (post-2015 wordmarks, sanctioned vs survivor):

- Dictionary frequency — the established `wordfreq` package's token
  zipf-frequency — separates the classes at AUC **0.77** (rarest-token,
  the evasion-robust variant: **0.75**); the corpus-fit character-n-gram
  coined score reaches AUC **0.78** whole-string (per-token variant
  **0.73**). The metrics are only weakly correlated (r ≈ 0.23), so they
  carry complementary information; report both.
- A **fixed, transparent weighting** of the per-filing signals (coinedness,
  marketplace class, goods-list length, basis, single-token, translation
  statements) — chosen a priori, not tuned — separates the classes at **test
  AUC ~0.89**. A logistic model *fit* to the same features reaches **~0.91**
  and is well-calibrated, so fitting adds only ~1.5 AUC points; nearly all
  the lift over a single signal comes from *combining* features, not fitting
  them. The paper therefore leads with the transparent composite (fixed,
  publishable, no overfitting critique) and uses the fitted model to validate
  it and to supply calibrated probabilities (ADR 0016). Both AUCs, the
  fitted model's calibration, and its cross-cohort coefficient stability are
  reported by `trademark-radar evaluate`.
- Each mark-string metric has two variants: an *evasion-robust* one
  (rarest-token zipf; max-over-tokens n-gram) that a filer cannot defeat by
  padding a coined token with a common word, and the *original* one that
  scores marginally higher on the current, un-adapted corpus. The robust
  variants trade a little clean-data accuracy for resistance to gaming; the
  combined model is unaffected. See docs/adr and the "Robustness to
  evasion" note below.
- Translation statements need semantic handling: *presence* of a
  translation/transliteration statement is anti-informative (filing
  services template in "no meaning in a foreign language" boilerplate —
  98.7% of statement-bearing sanctioned filings say this), while a *real*
  translation is strong evidence the mark is a meaningful foreign word
  rather than a coined string. This is also the control for the
  transliteration confound (a pinyin word is improbable under English
  letter statistics without being coined).

**Design constraints, stated plainly:**

- Models are fit only on **label-complete cohorts** (filings 2015–2018):
  the survivor label requires a §8 window 5–6 years after registration, so
  recent filing years cannot contain negatives, and a naive temporal split
  measures label availability rather than signal quality.
- The labels come from enforcement and maintenance processes, **not random
  audits**. Prevalence in the labeled pool is not population prevalence,
  and the sanctioned set over-represents conduct that was detectable at
  scale. Metrics here validate that the signals separate *adjudicated*
  populations; they do not estimate a base rate of problematic filing.

## Event study: composition breaks at policy discontinuities

Three policy events plausibly shaped the surge — the Amazon Brand Registry
relaunch (~2017-04), the USPTO U.S.-counsel rule (2019-08-03), and the
Trademark Modernization Act's expungement/reexamination provisions
(2021-12-18). `trademark-radar event-study`
(`uspto_trademark_radar.gold.event_study`) builds a monthly composition
series and analyzes it two ways, keeping description and causation apart.

**The behavioral composition** (coined share, marketplace-class share,
foreign-owner share, use-basis, goods-list length, volume) is fit with a
descriptive interrupted time series — a level and slope change at each date,
month-of-year seasonality, volume-weighted, with Newey-West errors. The
honest headline is that **the single largest break is 2020**, coincident
with the pandemic e-commerce boom rather than any policy date. Because 2020
sits mid-segment, the post-2019 slope estimates absorb it, so the policy-date
coefficients are read as *description, not causal effect*. What the series
does show cleanly: Brand Registry marks a gradual onset of the coined-share
trend (a significant positive slope change, no discontinuous jump — a soft
rollout), and the behavioral metrics break significantly *downward* around
the TMA date (a post-surge cooling that cannot be separated from ordinary
pandemic mean-reversion and is not claimed as a TMA effect).

**The U.S.-counsel rule's mechanical effect** is, by contrast, cleanly
identified. The rule requires foreign-domiciled applicants to appoint a
U.S.-licensed attorney, so it mechanically raises attorney-of-record
presence for foreign filers. A difference-in-differences — foreign (CN)
applicants treated, domestic (US) applicants control — estimates that effect
at **+0.24** (attorney-present rate: CN 0.50→0.73 after the rule, US
0.69→0.67; robust p < 0.001), alongside a **4.4×** anticipatory filing rush
in the month before the deadline. This is regulatory compliance, not filing
behavior, and it is kept out of the behavioral read entirely — an attorney
appearing on a post-2019 foreign filing is the law, not a signal.

The monthly series is aggregate (no person-level data) and reproducible from
the published dataset.

## Reading the results

These are descriptive statistics. Distributions overlap: high-volume filers
of many kinds — corporate trademark counsel, DIY-filing services — share
surface features and are separated only imperfectly (mainly by the
client-relationship signal in query 4), so a high score on any axis reflects a
portfolio's composition, not a conclusion about the filer. Treat outputs as
starting points for inspecting the underlying filings, not as findings.
