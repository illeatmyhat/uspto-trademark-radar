"""Event-study module: segmented ITS and the US-counsel DiD on synthetic
series (exact known effects), plus build_series on the XML fixtures.
All owners/dates are fictional test data."""
import pytest

pytest.importorskip("statsmodels")
pytest.importorskip("pandas")

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

from conftest import make_case_file, make_xml, make_zip   # noqa: E402
from uspto_trademark_radar import parse_stage             # noqa: E402
from uspto_trademark_radar.build import build_candidate    # noqa: E402
from uspto_trademark_radar.gold import event_study, mark_features  # noqa: E402
from uspto_trademark_radar.gold.coined_scorer import CoinedScorer  # noqa: E402
from uspto_trademark_radar.integrity import sha256_file    # noqa: E402

_REFERENCE = """
apple orange banana table chair window garden flower morning evening
water river mountain forest bridge castle silver golden simple gentle
market bright shadow thunder harvest wonder journey lantern harbor
premium natural organic classic modern comfort quality wedding titan
""".split() * 20

_RULE = pd.Timestamp("2019-08-01")


def _monthly(start: str, periods: int) -> pd.DataFrame:
    ym = pd.date_range(start, periods=periods, freq="MS")
    return pd.DataFrame({"ym": ym, "n": 1000.0})


def test_its_recovers_a_known_level_break():
    df = _monthly("2015-01-01", 96)       # 2015-01 .. 2022-12
    step = (df["ym"] >= _RULE).astype(float)
    # a clean +0.05 level jump at the US-counsel date, tiny non-seasonal wiggle
    df["coined_rate"] = 0.10 + 0.05 * step + 0.001 * np.sin(np.arange(len(df)))
    r = event_study._fit_its(df, "coined_rate")
    b = r["breaks"]
    assert 0.03 < b["us_counsel"]["level_shift"] < 0.07     # recovers ~0.05
    assert b["us_counsel"]["level_p"] < 0.05
    # the other two dates carry no injected effect
    assert abs(b["brand_registry"]["level_shift"]) < 0.02
    assert abs(b["tma"]["level_shift"]) < 0.02
    # us_counsel is the dominant break by level magnitude
    assert abs(b["us_counsel"]["level_shift"]) == max(
        abs(b[k]["level_shift"]) for k in b)


def test_did_recovers_counsel_rule_effect_and_rush():
    df = _monthly("2018-01-01", 36)       # 2018-01 .. 2020-12
    post = (df["ym"] >= pd.Timestamp("2019-09-01")).astype(float)
    df["atty_cn"] = 0.55 + 0.23 * post    # treated: +0.23 after the rule
    df["atty_us"] = 0.68                  # control: flat
    df["n_cn"] = 5000.0
    df.loc[df["ym"] == pd.Timestamp("2019-07-01"), "n_cn"] = 20000.0  # rush
    df["n_us"] = 8000.0

    did = event_study.did_counsel_rule(df)
    assert abs(did["did_estimate"] - 0.23) < 0.01
    assert did["did_p"] < 0.01
    assert abs(did["cn_pre"] - 0.55) < 0.001 and abs(did["cn_post"] - 0.78) < 0.001
    assert abs(did["us_pre"] - 0.68) < 0.001 and abs(did["us_post"] - 0.68) < 0.001
    assert abs(did["pre_deadline_rush_ratio"] - 4.0) < 0.2


def _build(cfg, ledger, case_files, name="apc18840407-20241231-01.zip"):
    z = make_zip(cfg.bronze_annual / name, make_xml(case_files))
    ledger.record_download(name, "TRTYRAP", "http://t", z.stat().st_size,
                           sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


def test_build_series_is_monthly_and_trims_incomplete_tail(cfg, ledger):
    import duckdb
    cases, sid = [], 80000000
    for mo in ("01", "02", "03"):         # 3 complete months, 3 filings each
        for _ in range(3):
            cases.append(make_case_file(str(sid), filing=f"2019{mo}05"))
            sid += 1
    for mo in ("04", "05"):               # 2 later months -> trimmed as tail
        cases.append(make_case_file(str(sid), filing=f"2019{mo}05"))
        sid += 1
    candidate = _build(cfg, ledger, cases)
    gold_root = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(_REFERENCE)
    mark_features.build(candidate, gold_root, scorer=scorer, log=lambda *a: None)

    out = event_study.build_series(candidate, gold_root, log=lambda *a: None)
    rows = duckdb.connect().execute(
        f"SELECT strftime(ym,'%Y-%m'), n FROM "
        f"read_parquet('{out.as_posix()}/*.parquet') ORDER BY 1").fetchall()
    assert [r[0] for r in rows] == ["2019-01", "2019-02", "2019-03"]
    assert all(r[1] == 3 for r in rows)
