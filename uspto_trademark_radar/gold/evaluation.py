"""Evaluation against USPTO-adjudicated outcomes.

Builds a labeled table from outcomes the Office itself recorded, then
measures how well the mark-level signals separate them. Two outcome
families are deliberately kept apart and never pooled:

- **sanctions** (order-for-sanctions / terminated-after-sanctions events,
  status 610): a filing-conduct outcome.
- **TMA expungement/reexamination institution** (proceeding-instituted
  events, post-2021): a nonuse outcome. Ordinary marks that simply fell out
  of use belong here, not with filing-conduct cases — measured AUCs confirm
  the two populations look nothing alike on mark-composition signals.

**survivor** (continued-use affidavit accepted on a registration) serves as
the negative class for the sanctions comparison.

Model fitting uses only label-complete cohorts (LABEL_COMPLETE_YEARS):
the survivor label requires a Section 8 window 5-6 years after
registration, so recent filing years cannot contain negatives and any
naive temporal split is biased by label availability alone. Prevalence in
the labeled pool is NOT population prevalence — labels come from
enforcement and maintenance processes, not random audits.

Requires the `analysis` dependency group (wordfreq, scikit-learn) for
`evaluate`; `build_labels` needs only the core dependencies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, UTC
from pathlib import Path

import duckdb

from ..db import one_row

VERSION = "1"
TABLE = f"evaluation_labels_v{VERSION}"

SANCTION_EVENTS = ("KBOC", "KOFS", "KONO")
SANCTION_STATUS = "610"
TMA_INSTITUTED_EVENTS = ("BXPI", "BRPI")
TMA_DENIED_EVENTS = ("BDXD", "BPXX", "BDRD", "BPRD")

# Filing years where both the sanction and survivor labels have had time to
# materialize (sanctions ran 2021-22; Section 8 windows land 5-6 years after
# registration). Widen as the corpus ages.
LABEL_COMPLETE_YEARS = (2015, 2018)

_MIN_CLASS_FOR_AUC = 30
_MIN_CLASS_FOR_MODEL = 100
_TOKENS = re.compile(r"[a-z']+")

# Fixed, a-priori weighting over the standardized per-filing features - the
# transparent composite the paper leads with (ADR 0016). Chosen from the
# coined-mark thesis, NOT fit: coinedness dominates (rarest-token zipf +
# n-gram), marketplace and goods-padding contribute, a real translation is
# the anti-signal. `evaluate` reports its AUC beside the fitted model to show
# fitting adds little.
TRANSPARENT_WEIGHTS = {
    "neg_min_zipf": 0.20, "ngram_coined": 0.25, "single_token": 0.10,
    "standard_char": 0.05, "marketplace_class": 0.20, "goods_items": 0.10,
    "use_basis": 0.10, "real_translation": -0.20,
}


def _rp(build_dir: Path, tbl: str) -> str:
    return f"read_parquet('{(build_dir / 'data' / tbl).as_posix()}/*.parquet')"


def _labels_sql(build_dir: Path) -> str:
    sanction_ev = ", ".join(f"'{c}'" for c in SANCTION_EVENTS)
    tma_inst = ", ".join(f"'{c}'" for c in TMA_INSTITUTED_EVENTS)
    tma_denied = ", ".join(f"'{c}'" for c in TMA_DENIED_EVENTS)
    return f"""
    WITH sanctioned AS (
      SELECT DISTINCT serial_no FROM {_rp(build_dir, 'event')}
      WHERE event_cd IN ({sanction_ev})
      UNION
      SELECT serial_no FROM {_rp(build_dir, 'case_file')}
      WHERE status_cd = '{SANCTION_STATUS}'),
    tma_instituted AS (
      SELECT DISTINCT serial_no FROM {_rp(build_dir, 'event')}
      WHERE event_cd IN ({tma_inst})),
    tma_denied AS (
      SELECT DISTINCT serial_no FROM {_rp(build_dir, 'event')}
      WHERE event_cd IN ({tma_denied})),
    survivor AS (
      SELECT serial_no FROM {_rp(build_dir, 'case_file')}
      WHERE use_afdv_acc_in AND registration_dt IS NOT NULL)
    SELECT cf.serial_no,
           year(cf.filing_dt) AS filing_year,
           CASE
             WHEN cf.serial_no IN (SELECT serial_no FROM sanctioned)
               THEN 'sanctioned'
             WHEN cf.serial_no IN (SELECT serial_no FROM tma_instituted)
               THEN 'tma_instituted'
             WHEN cf.serial_no IN (SELECT serial_no FROM tma_denied)
               THEN 'tma_denied'
             WHEN cf.serial_no IN (SELECT serial_no FROM survivor)
               THEN 'survivor'
           END AS label
    FROM {_rp(build_dir, 'case_file')} cf
    WHERE label IS NOT NULL
    """


def build_labels(silver_build_dir: Path, gold_root: Path, log=print) -> Path:
    """Materialize the outcome-labeled table under gold_root/<TABLE>/."""
    con = duckdb.connect()
    out_dir = gold_root / TABLE
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.iterdir():
        stale.unlink()
    con.execute(f"""
      COPY ({_labels_sql(silver_build_dir)} ORDER BY serial_no)
      TO '{out_dir.as_posix()}/part-000.parquet'
      (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    counts = dict(con.execute(
        f"SELECT label, count(*) FROM "
        f"read_parquet('{out_dir.as_posix()}/*.parquet') GROUP BY 1"
    ).fetchall())
    meta = {
        "table": TABLE, "version": VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "label_counts": counts,
        "codes": {"sanction_events": SANCTION_EVENTS,
                  "sanction_status": SANCTION_STATUS,
                  "tma_instituted_events": TMA_INSTITUTED_EVENTS,
                  "tma_denied_events": TMA_DENIED_EVENTS},
    }
    (out_dir / "_lineage.json").write_text(json.dumps(meta, indent=2))
    con.close()
    log(f"[eval] {TABLE}: " + ", ".join(f"{k}={v:,}" for k, v in sorted(counts.items())))
    return out_dir


def _token_zipf(mark: str, agg: str) -> float:
    """max or min English zipf-frequency over the mark's tokens. min (rarest
    token) resists the append-a-common-word evasion; max is the original,
    evadable axis, kept for comparison. 0.0 when no alpha tokens."""
    from wordfreq import zipf_frequency

    freqs = [zipf_frequency(t, "en") for t in _TOKENS.findall(mark.lower())]
    if not freqs:
        return 0.0
    return max(freqs) if agg == "max" else min(freqs)


def evaluate(silver_build_dir: Path, gold_root: Path, log=print) -> dict:
    """Measure signal quality against the sanctions/survivor labels and fit
    the reference logistic model on label-complete cohorts. Writes
    gold_root/evaluation_metrics.json and returns the dict."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
    except ImportError as e:
        raise SystemExit(
            "evaluate needs the analysis dependency group - run "
            "`uv sync --group analysis`"
        ) from e

    labels_glob = (gold_root / TABLE).as_posix() + "/*.parquet"
    mf_glob = (gold_root / "mark_features_v1").as_posix() + "/*.parquet"
    con = duckdb.connect()
    rows = con.execute(f"""
        WITH real_tr AS (
          SELECT serial_no,
            bool_or((statement_text ILIKE '%mean%'
                     AND statement_text NOT ILIKE '%no meaning%')
                 OR (statement_text ILIKE '%translat%'
                     AND statement_text NOT ILIKE '%no%translat%')) AS has_real_tr
          FROM {_rp(silver_build_dir, 'statement')}
          WHERE statement_type_cd LIKE 'TR%' OR statement_type_cd LIKE 'TLIT%'
          GROUP BY serial_no)
        SELECT l.label, m.mark_id_char,
               coalesce(m.coined_token_max, m.coined_mark_score),
               m.coined_mark_score,
               coalesce(m.single_token::INT, 0),
               coalesce(m.is_standard_char::INT, 0),
               coalesce(m.is_marketplace_class::INT, 0),
               coalesce(m.goods_item_count, 0),
               coalesce((m.filing_basis = 'use')::INT, 0),
               coalesce(t.has_real_tr, FALSE)::INT,
               m.filing_year
        FROM read_parquet('{labels_glob}') l
        JOIN read_parquet('{mf_glob}') m USING (serial_no)
        LEFT JOIN real_tr t USING (serial_no)
        WHERE l.label IN ('sanctioned', 'survivor')
          AND m.coined_token_max IS NOT NULL AND m.filing_year >= 2015
    """).fetchall()
    n_rows = one_row(con, f"SELECT count(*) FROM read_parquet('{labels_glob}')")[0]
    con.close()

    y = np.array([r[0] == "sanctioned" for r in rows], dtype=int)
    metrics: dict = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "labeled_rows_total": n_rows,
        "pool": {"n": len(rows), "sanctioned": int(y.sum())},
        "note": ("labels are enforcement/maintenance outcomes, not random "
                 "audits; prevalence in this pool is not population "
                 "prevalence"),
    }
    if min(y.sum(), len(y) - y.sum()) < _MIN_CLASS_FOR_AUC:
        metrics["status"] = "insufficient labeled data for AUC/model"
        _write(gold_root, metrics, log)
        return metrics

    ngram = np.array([r[2] for r in rows], dtype=float)
    ngram_whole = np.array([r[3] for r in rows], dtype=float)
    max_zipf = np.array([_token_zipf(r[1], "max") for r in rows])
    min_zipf = np.array([_token_zipf(r[1], "min") for r in rows])
    years = np.array([r[10] for r in rows])
    # Each metric has an evasion-robust and an original (more accurate on
    # un-gamed data) variant; report both. min-zipf / per-token-max resist
    # padding a coined token with a common word; max-zipf / whole-string do
    # not but score marginally higher on the current, un-adapted corpus.
    metrics["auc"] = {
        "wordfreq_low_min_zipf": round(float(roc_auc_score(y, -min_zipf)), 4),
        "wordfreq_low_max_zipf": round(float(roc_auc_score(y, -max_zipf)), 4),
        "ngram_coined_token_max": round(float(roc_auc_score(y, ngram)), 4),
        "ngram_coined_whole": round(float(roc_auc_score(y, ngram_whole)), 4),
        "metric_correlation_r": round(float(np.corrcoef(ngram, -min_zipf)[0, 1]), 4),
    }

    feats = np.column_stack([
        -min_zipf, ngram,
        [r[4] for r in rows], [r[5] for r in rows], [r[6] for r in rows],
        np.minimum([r[7] for r in rows], 50) / 50.0,
        [r[8] for r in rows], [r[9] for r in rows],
    ])
    names = ["neg_min_zipf", "ngram_coined", "single_token", "standard_char",
             "marketplace_class", "goods_items", "use_basis",
             "real_translation"]
    lo, hi = LABEL_COMPLETE_YEARS
    window = (years >= lo) & (years <= hi)
    rng = np.random.default_rng(42)
    test_mask = rng.random(len(y)) < 0.3
    train, test = window & ~test_mask, window & test_mask
    if min(y[train].sum(), y[test].sum()) < _MIN_CLASS_FOR_MODEL:
        metrics["model"] = {"status": "insufficient positives in the "
                                      "label-complete window"}
        _write(gold_root, metrics, log)
        return metrics

    mu, sd = feats[train].mean(0), feats[train].std(0) + 1e-9
    clf = LogisticRegression(max_iter=1000).fit((feats[train] - mu) / sd, y[train])
    prob = clf.predict_proba((feats[test] - mu) / sd)[:, 1]
    metrics["model"] = {
        "design": f"logistic, label-complete cohorts {lo}-{hi}, "
                  "stratified 70/30 holdout, standardized features",
        "train": {"n": int(train.sum()), "positives": int(y[train].sum())},
        "test": {"n": int(test.sum()), "positives": int(y[test].sum())},
        "test_auc": round(float(roc_auc_score(y[test], prob)), 4),
        "standardized_coefficients": {
            n: round(float(c), 4)
            for n, c in sorted(zip(names, clf.coef_[0], strict=True),
                               key=lambda t: -abs(t[1]))},
    }

    # Head-to-head (ADR 0016): the fixed transparent weighting vs. the fitted
    # model on the same standardized holdout, plus the fitted model's
    # calibration and cross-cohort coefficient stability. The paper leads with
    # the transparent composite; this quantifies what fitting does and does
    # not buy.
    from sklearn.metrics import brier_score_loss

    x_test = (feats[test] - mu) / sd
    w = np.array([TRANSPARENT_WEIGHTS[n] for n in names])
    comparison: dict = {
        "decision": "transparent composite leads; fitted model validates "
                    "(ADR 0016)",
        "fitted_test_auc": metrics["model"]["test_auc"],
        "transparent_test_auc": round(float(roc_auc_score(y[test], x_test @ w)), 4),
        "equal_weight_test_auc": round(float(roc_auc_score(y[test], x_test.sum(1))), 4),
        "fitted_brier": round(float(brier_score_loss(y[test], prob)), 4),
        "transparent_weights": TRANSPARENT_WEIGHTS,
        "reading": "fitting adds little over a fixed transparent weighting; "
                   "the transparent composite is the primary instrument and "
                   "the fitted model validates it and supplies calibrated "
                   "probabilities",
    }
    mid = (lo + hi) // 2
    early, late = train & (years <= mid), train & (years > mid)
    te_late, te_early = test & (years > mid), test & (years <= mid)

    def _fit_cross(fit_mask, eval_mask):
        m2, s2 = feats[fit_mask].mean(0), feats[fit_mask].std(0) + 1e-9
        c = LogisticRegression(max_iter=1000).fit((feats[fit_mask] - m2) / s2, y[fit_mask])
        auc = roc_auc_score(y[eval_mask], c.predict_proba((feats[eval_mask] - m2) / s2)[:, 1])
        return c.coef_[0], round(float(auc), 4)

    if all(y[m].sum() and (m.sum() - y[m].sum()) for m in (early, late, te_late, te_early)):
        ce, auc_e = _fit_cross(early, te_late)
        cl, auc_l = _fit_cross(late, te_early)
        comparison["cross_cohort"] = {
            "split_year": mid,
            "early_fit_late_test_auc": auc_e,
            "late_fit_early_test_auc": auc_l,
            "coef_correlation": round(float(np.corrcoef(ce, cl)[0, 1]), 4),
            "note": "moderate coefficient drift is why the transparent, fixed "
                    "weighting is the headline instrument",
        }
    metrics["scorer_comparison"] = comparison
    _write(gold_root, metrics, log)
    return metrics


def _write(gold_root: Path, metrics: dict, log) -> None:
    path = gold_root / "evaluation_metrics.json"
    path.write_text(json.dumps(metrics, indent=2))
    log(f"[eval] metrics -> {path}")
