"""Date-policy gate: future expirations pass, out-of-policy dates fail only
above the 0.1% tolerance (a tiny fixture table makes any bad row exceed it).
"""

import pytest

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.integrity import sha256_file
from uspto_trademark_radar.reconcile import ReconcileError, reconcile

ANNUAL = "apc18840407-20241231-01.zip"


def _pipeline(cfg, ledger, case_files):
    z = make_zip(cfg.bronze_annual / ANNUAL, make_xml(case_files))
    ledger.record_download(ANNUAL, "TRTYRAP", "http://test",
                           z.stat().st_size, sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


def test_future_filing_date_fails_gate(cfg, ledger):
    bad = make_case_file("111", filing="20990101")  # past-only column
    candidate = _pipeline(cfg, ledger, [bad])
    with pytest.raises(ReconcileError, match="filing_dt"):
        reconcile(candidate, ledger, log=lambda *a: None)
    outliers = (candidate / "profile" / "date_outliers.csv").read_text()
    assert "case_file,filing_dt,0,1" in outliers


def test_pre_1870_filing_date_fails_gate(cfg, ledger):
    bad = make_case_file("111", filing="18230306")
    candidate = _pipeline(cfg, ledger, [bad])
    with pytest.raises(ReconcileError, match="filing_dt"):
        reconcile(candidate, ledger, log=lambda *a: None)
