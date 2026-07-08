"""event_study - policy-discontinuity analysis of the post-2015 surge (ADR 0015).

A monthly composition series of the corpus plus two identification strategies
around three policy discontinuities:

  - Amazon Brand Registry relaunch (2017-04) - a soft business rollout, not a
    legal date, so a gradual inflection rather than a sharp step.
  - USPTO US-counsel rule (2019-08-03) - sharp regulatory: foreign-domiciled
    applicants must appoint a US-licensed attorney.
  - Trademark Modernization Act expungement/reexamination (2021-12-18).

The *behavioral* composition metrics (coined share, marketplace-class share,
foreign-owner share, use-basis, goods-list length, volume) get a DESCRIPTIVE
interrupted-time-series: segmented regression with a level and slope change
at each date, month-of-year seasonality, volume weighting, and HAC
(Newey-West) errors. It is descriptive on purpose - the largest break is
2020, which coincides with the pandemic e-commerce boom rather than a policy
date, so breaks are characterized, not causally attributed.

The US-counsel rule's MECHANICAL effect is identified cleanly with
difference-in-differences: foreign (CN) applicants are the treated group,
domestic (US) applicants the control, and attorney-of-record presence the
outcome. This is the "model it, do not discover it" break - the post-2019
attorney-pattern shift is regulatory compliance, not behavior, so it never
enters the behavioral read. The pre-deadline filing rush is reported too.

Aggregate monthly statistics only (no person-level data), so the series is
publishable, unlike operation_profile. Deterministic given the build.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import duckdb

from ..db import one_row
from . import mark_features
from .operation_profile import MARKETPLACE_CLASSES

VERSION = "1"
TABLE = f"event_series_v{VERSION}"
METRICS_FILE = "event_study_metrics.json"

# Series starts here; the incomplete tail (recent months still filling in) is
# trimmed in build_series relative to the build's latest filing month.
SERIES_START = "2013-01-01"
TAIL_TRIM_MONTHS = 2

# (key, human label, month-start date). Dates snap to the month for the
# monthly series; the US-counsel effect materializes the following month.
BREAKS: list[tuple[str, str, str]] = [
    ("brand_registry", "Amazon Brand Registry (2017-04, soft rollout)", "2017-04-01"),
    ("us_counsel", "US-counsel rule (2019-08-03)", "2019-08-01"),
    ("tma", "TMA expungement/reexamination (2021-12-18)", "2021-12-01"),
]

# Behavioral composition metrics the descriptive ITS runs on. attorney
# presence is deliberately excluded - it is the regulatory/mechanical
# outcome handled by the DiD, never treated as behavior.
BEHAVIORAL_METRICS = (
    "coined_rate", "marketplace_rate", "cn_share", "use1a_rate",
    "mean_goods", "log_volume",
)

COINED_THRESHOLD = 0.85


def _rp(build_dir: Path, tbl: str) -> str:
    return f"read_parquet('{(build_dir / 'data' / tbl).as_posix()}/*.parquet')"


def build_series(silver_build_dir: Path, gold_root: Path, log=print) -> Path:
    """Materialize the monthly composition series under gold_root/<TABLE>/.
    Coined share reuses the persisted mark_features table (same build check
    as the rest of gold). Aggregate-only; publishable."""
    mf_dir = gold_root / mark_features.TABLE
    lineage = mf_dir / "_lineage.json"
    if lineage.exists():
        mf_build = json.loads(lineage.read_text()).get("silver_build")
        if mf_build and mf_build != silver_build_dir.name:
            raise SystemExit(
                f"mark_features was built from {mf_build!r} but the event study "
                f"targets {silver_build_dir.name!r}; rebuild gold first")
    mf_glob = mf_dir.as_posix() + "/*.parquet"

    con = duckdb.connect()
    last_month = one_row(
        con, f"SELECT date_trunc('month', max(filing_dt)) FROM "
             f"{_rp(silver_build_dir, 'case_file')}")[0]
    # first month whose data is still filling in - series stops before it
    end_exclusive = f"(DATE '{last_month}' - INTERVAL {TAIL_TRIM_MONTHS - 1} MONTH)"

    out_dir = gold_root / TABLE
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.iterdir():
        stale.unlink()

    con.execute(f"""
      COPY (
        WITH owner_pick AS (
          SELECT serial_no, own_addr_country_cd cc FROM (
            SELECT serial_no, own_addr_country_cd,
              row_number() OVER (PARTITION BY serial_no
                ORDER BY (own_type_cd='10') DESC, own_seq, own_name) rn
            FROM {_rp(silver_build_dir, 'owner')}) WHERE rn = 1),
        mkt AS (SELECT DISTINCT serial_no FROM {_rp(silver_build_dir, 'intl_class')}
                WHERE intl_class_cd IN {MARKETPLACE_CLASSES}),
        goods AS (SELECT serial_no,
                    max(length(statement_text)-length(replace(statement_text,';',''))+1) gi
                  FROM {_rp(silver_build_dir, 'statement')}
                  WHERE statement_type_cd LIKE 'GS%' GROUP BY 1)
        SELECT date_trunc('month', cf.filing_dt) AS ym,
          count(*) AS n,
          avg((coalesce(m.coined_token_max,0) >= {COINED_THRESHOLD})::INT) AS coined_rate,
          avg((k.serial_no IS NOT NULL)::INT) AS marketplace_rate,
          avg((o.cc='CN')::INT) AS cn_share,
          avg(cf.use_af_in::INT) AS use1a_rate,
          avg(g.gi) AS mean_goods,
          avg((cf.attorney_name IS NOT NULL)::INT) AS atty_rate,
          avg((cf.attorney_name IS NOT NULL)::INT) FILTER (WHERE o.cc='CN') AS atty_cn,
          avg((cf.attorney_name IS NOT NULL)::INT) FILTER (WHERE o.cc='US') AS atty_us,
          count(*) FILTER (WHERE o.cc='CN') AS n_cn,
          count(*) FILTER (WHERE o.cc='US') AS n_us
        FROM {_rp(silver_build_dir, 'case_file')} cf
        LEFT JOIN read_parquet('{mf_glob}') m USING (serial_no)
        LEFT JOIN owner_pick o USING (serial_no)
        LEFT JOIN mkt k USING (serial_no)
        LEFT JOIN goods g USING (serial_no)
        WHERE cf.filing_dt >= DATE '{SERIES_START}'
          AND date_trunc('month', cf.filing_dt) < {end_exclusive}
        GROUP BY 1 ORDER BY 1
      ) TO '{out_dir.as_posix()}/part-000.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_months = one_row(
        con, f"SELECT count(*) FROM read_parquet('{out_dir.as_posix()}/*.parquet')")[0]
    span = one_row(
        con, f"SELECT min(ym), max(ym) FROM read_parquet('{out_dir.as_posix()}/*.parquet')")
    meta = {
        "table": TABLE, "version": VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "n_months": n_months,
        "span": [str(span[0]), str(span[1])],
        "tail_trim_months": TAIL_TRIM_MONTHS,
        "scope": "aggregate monthly composition; publishable (no person-level).",
    }
    (out_dir / "_lineage.json").write_text(json.dumps(meta, indent=2))
    con.close()
    log(f"[event] {TABLE}: {n_months} months {span[0]}..{span[1]} -> {out_dir}")
    return out_dir


def _fit_its(df, metric: str, maxlags: int = 12) -> dict:
    """Segmented interrupted-time-series for one metric: a level shift and a
    slope change at each break, month-of-year seasonality, volume-weighted
    (WLS on monthly volume), HAC/Newey-West covariance. Pure (takes a
    DataFrame); returns per-break effect estimates."""
    import numpy as np
    import pandas as pd
    import statsmodels.api as sm

    d = df.reset_index(drop=True)
    n = len(d)
    t = np.arange(n)
    cols: dict[str, np.ndarray] = {"const": np.ones(n), "trend": t.astype(float)}
    for key, _, date in BREAKS:
        mask = (d["ym"] >= pd.Timestamp(date)).to_numpy()
        tk = int(mask.argmax()) if mask.any() else n
        cols[f"level_{key}"] = (t >= tk).astype(float)
        cols[f"slope_{key}"] = np.maximum(t - tk, 0).astype(float)
    moy = d["ym"].dt.month.to_numpy()
    for m in range(2, 13):                       # January is the reference
        cols[f"seas_{m}"] = (moy == m).astype(float)
    x = pd.DataFrame(cols)
    y = d[metric].astype(float).to_numpy()
    w = d["n"].astype(float).to_numpy()
    res = sm.WLS(y, x, weights=w).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})

    breaks = {}
    for key, label, _ in BREAKS:
        breaks[key] = {
            "label": label,
            "level_shift": round(float(res.params[f"level_{key}"]), 5),
            "level_se": round(float(res.bse[f"level_{key}"]), 5),
            "level_p": round(float(res.pvalues[f"level_{key}"]), 4),
            "slope_change_per_month": round(float(res.params[f"slope_{key}"]), 6),
            "slope_se": round(float(res.bse[f"slope_{key}"]), 6),
            "slope_p": round(float(res.pvalues[f"slope_{key}"]), 4),
        }
    return {"metric": metric, "n_months": n,
            "r2": round(float(res.rsquared), 4), "breaks": breaks}


def did_counsel_rule(df, window_months: int = 10) -> dict:
    """Difference-in-differences for the US-counsel rule's mechanical effect
    on attorney-of-record presence: foreign (CN) applicants treated, domestic
    (US) applicants control, transition month (2019-08) dropped, effect from
    2019-09. Volume-weighted WLS, robust (HC1) errors. Pure; also returns the
    pre-deadline filing-rush ratio."""
    import pandas as pd
    import statsmodels.api as sm

    rule = pd.Timestamp("2019-08-01")
    post_start = pd.Timestamp("2019-09-01")
    off = pd.DateOffset(months=window_months)
    lo, hi = rule - off, post_start + off      # type: ignore  (Timestamp -/+ offset)
    win = df[(df["ym"] >= lo) & (df["ym"] < hi) & (df["ym"] != rule)]

    cols: dict[str, list[float]] = {"y": [], "cn": [], "post": [], "n": []}
    for _, r in win.iterrows():
        post = 1.0 if r["ym"] >= post_start else 0.0
        cols["y"] += [r["atty_cn"], r["atty_us"]]
        cols["cn"] += [1.0, 0.0]
        cols["post"] += [post, post]
        cols["n"] += [r["n_cn"], r["n_us"]]
    panel = pd.DataFrame(cols).dropna()
    x = pd.DataFrame({
        "const": 1.0, "cn": panel["cn"], "post": panel["post"],
        "cn_x_post": panel["cn"] * panel["post"],
    })
    yv = panel["y"].astype(float).to_numpy()
    wv = panel["n"].astype(float).to_numpy()
    # statsmodels is untyped, so pyright infers weights as float from its
    # default; the real array is fine at runtime.
    res = sm.WLS(yv, x, weights=wv).fit(  # pyright: ignore[reportArgumentType]
        cov_type="HC1")

    def wmean(mask) -> float:
        s = panel[mask]
        return round(float((s["y"] * s["n"]).sum() / s["n"].sum()), 4)

    jul = df.loc[df["ym"] == pd.Timestamp("2019-07-01"), "n_cn"]
    prior = df[(df["ym"] >= pd.Timestamp("2018-07-01"))
               & (df["ym"] < pd.Timestamp("2019-07-01"))]["n_cn"]
    rush = (round(float(jul.iloc[0] / prior.median()), 2)
            if len(jul) and prior.median() else None)

    return {
        "outcome": "attorney_of_record_present",
        "design": f"DiD, CN treated vs US control, +/-{window_months} months "
                  "around 2019-08-03, transition month dropped, WLS by volume",
        "did_estimate": round(float(res.params["cn_x_post"]), 4),
        "did_se": round(float(res.bse["cn_x_post"]), 4),
        "did_p": round(float(res.pvalues["cn_x_post"]), 4),
        "cn_pre": wmean((panel["cn"] == 1) & (panel["post"] == 0)),
        "cn_post": wmean((panel["cn"] == 1) & (panel["post"] == 1)),
        "us_pre": wmean((panel["cn"] == 0) & (panel["post"] == 0)),
        "us_post": wmean((panel["cn"] == 0) & (panel["post"] == 1)),
        "pre_deadline_rush_ratio": rush,
        "rush_note": "CN filings in 2019-07 vs the prior-12-month median",
    }


def analyze(silver_build_dir: Path, gold_root: Path, log=print) -> dict:
    """Build the series if needed, run the descriptive ITS on the behavioral
    metrics and the DiD on the US-counsel rule, write
    gold_root/event_study_metrics.json, and return the dict."""
    try:
        import numpy as np
        import pandas as pd
    except ImportError as e:
        raise SystemExit("event-study needs the analysis dependency group - "
                         "run `uv sync --group analysis`") from e

    if not (gold_root / TABLE / "_lineage.json").exists():
        build_series(silver_build_dir, gold_root, log=log)
    df = pd.read_parquet((gold_root / TABLE).as_posix())
    df["ym"] = pd.to_datetime(df["ym"])
    df = df.sort_values("ym").reset_index(drop=True)
    df["log_volume"] = np.log(df["n"])

    its = {m: _fit_its(df, m) for m in BEHAVIORAL_METRICS}
    did = did_counsel_rule(df)
    metrics = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "n_months": int(len(df)),
        "span": [str(df["ym"].min().date()), str(df["ym"].max().date())],
        "breaks": [{"key": k, "label": lb, "date": dt} for k, lb, dt in BREAKS],
        "framing": (
            "Behavioral-metric breaks are DESCRIPTIVE (segmented ITS): the "
            "largest break is 2020, coincident with the pandemic e-commerce "
            "surge and not a policy date, so breaks are characterized, not "
            "causally attributed. Only the US-counsel rule's mechanical "
            "attorney-presence effect is causally identified (DiD)."),
        "behavioral_its": its,
        "us_counsel_did": did,
    }
    out = gold_root / METRICS_FILE
    out.write_text(json.dumps(metrics, indent=2))
    log(f"[event] metrics -> {out}")
    return metrics
