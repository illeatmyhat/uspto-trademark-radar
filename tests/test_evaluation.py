"""Outcome labels: sanctions vs TMA nonuse vs survivors, never pooled."""
import json

import duckdb

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.gold import evaluation
from uspto_trademark_radar.integrity import sha256_file


def _build(cfg, ledger, case_files, name="apc18840407-20241231-01.zip"):
    z = make_zip(cfg.bronze_annual / name, make_xml(case_files))
    ledger.record_download(name, "TRTYRAP", "http://t", z.stat().st_size,
                           sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


_EV = ('<case-file-event-statement><code>{code}</code><type>O</type>'
       '<date>20220301</date><number>9</number></case-file-event-statement>')


def test_labels_separate_conduct_from_nonuse(cfg, ledger):
    cases = [
        # sanctioned via event
        make_case_file("60000001", mark="QVOWII",
                       extra_events=_EV.format(code="KBOC")),
        # sanctioned via status 610
        make_case_file("60000002", mark="ZXQWJ").replace(
            "<status-code>630</status-code>", "<status-code>610</status-code>"),
        # TMA expungement instituted — a nonuse outcome, NOT filing-conduct
        make_case_file("60000003", mark="GARDEN TABLE",
                       extra_events=_EV.format(code="BXPI")),
        # survivor: registered + section 8 accepted
        make_case_file("60000004", mark="SILVER RIVER", header_extra=(
            "<registration-date>20210105</registration-date>"
            "<section-8-accepted-in>T</section-8-accepted-in>")),
        # unlabeled
        make_case_file("60000005", mark="MORNING HARBOR"),
    ]
    candidate = _build(cfg, ledger, cases)
    out = evaluation.build_labels(candidate, cfg.data_root / "gold",
                                  log=lambda *a: None)

    con = duckdb.connect()
    labels = dict(con.execute(
        f"SELECT serial_no, label FROM read_parquet('{out.as_posix()}/*.parquet')"
    ).fetchall())
    assert labels == {"60000001": "sanctioned", "60000002": "sanctioned",
                      "60000003": "tma_instituted", "60000004": "survivor"}

    meta = json.loads((out / "_lineage.json").read_text())
    assert meta["label_counts"] == {"sanctioned": 2, "tma_instituted": 1,
                                    "survivor": 1}


def test_evaluate_degrades_gracefully_on_tiny_corpora(cfg, ledger):
    import pytest
    pytest.importorskip("sklearn")
    pytest.importorskip("wordfreq")
    from uspto_trademark_radar.gold import mark_features
    from uspto_trademark_radar.gold.coined_scorer import CoinedScorer

    cases = [
        make_case_file("60000001", mark="QVOWII",
                       extra_events=_EV.format(code="KBOC")),
        make_case_file("60000004", mark="SILVER RIVER", header_extra=(
            "<registration-date>20210105</registration-date>"
            "<section-8-accepted-in>T</section-8-accepted-in>")),
    ]
    candidate = _build(cfg, ledger, cases)
    gold = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(["silver", "river", "garden"] * 40)
    mark_features.build(candidate, gold, scorer=scorer, log=lambda *a: None)
    evaluation.build_labels(candidate, gold, log=lambda *a: None)

    metrics = evaluation.evaluate(candidate, gold, log=lambda *a: None)
    assert metrics["pool"]["sanctioned"] == 1
    assert metrics["status"].startswith("insufficient")
    assert (gold / "evaluation_metrics.json").exists()
