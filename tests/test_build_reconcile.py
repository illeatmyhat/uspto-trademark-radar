"""End-to-end on fixtures: annual snapshot + newer daily -> candidate build.
Verifies latest-wins on serial_no, wholesale child-row replacement,
reconcile gates, release swap and daily pruning.
"""

import duckdb
import pytest

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage, release
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.integrity import sha256_file
from uspto_trademark_radar.reconcile import ReconcileError, reconcile

ANNUAL = "apc18840407-20241231-01.zip"
DAILY_OLD = "apc241230.zip"   # ≤ cutoff: must be ignored and pruned
DAILY_NEW = "apc250301.zip"   # > cutoff: must win over the annual


def _stage_bronze(cfg, ledger):
    zips = {
        (ANNUAL, "TRTYRAP"): make_zip(cfg.bronze_annual / ANNUAL, make_xml([
            make_case_file("75930757", mark="VOFULY"),
            make_case_file("75930758", mark="QUIMZOR"),
        ])),
        (DAILY_OLD, "TRTDXFAP"): make_zip(cfg.bronze_daily / DAILY_OLD, make_xml([
            make_case_file("75930757", mark="STALE-SHOULD-NEVER-APPEAR"),
        ])),
        (DAILY_NEW, "TRTDXFAP"): make_zip(cfg.bronze_daily / DAILY_NEW, make_xml([
            make_case_file("75930757", mark="VOFULY-2",
                           extra_owner="New Owner LLC",
                           statement="Updated goods text"),
        ])),
    }
    # Real hashes so the parse stage's at-rest verification path runs.
    for (name, product), path in zips.items():
        ledger.record_download(name, product, "http://test",
                               path.stat().st_size, sha256_file(path))


def _run_pipeline(cfg, ledger):
    todo = parse_stage.plan(cfg, ledger)
    assert {p.name for p in todo} == {ANNUAL, DAILY_NEW}  # old daily excluded
    done, failed = parse_stage.work(cfg, ledger, log=lambda *a: None)
    assert (done, failed) == (2, 0)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


def test_latest_wins_and_child_replacement(cfg, ledger):
    _stage_bronze(cfg, ledger)
    candidate = _run_pipeline(cfg, ledger)

    con = duckdb.connect()
    cf = (candidate / "data" / "case_file").as_posix() + "/*.parquet"
    rows = dict(con.execute(
        f"SELECT serial_no, mark_id_char FROM read_parquet('{cf}')"
    ).fetchall())
    assert rows == {"75930757": "VOFULY-2", "75930758": "QUIMZOR"}

    owners = con.execute(
        f"SELECT serial_no, own_type_cd, own_name FROM read_parquet('"
        f"{(candidate / 'data' / 'owner').as_posix()}/*.parquet') "
        f"WHERE serial_no = '75930757' ORDER BY own_type_cd"
    ).fetchall()
    # child rows come wholesale from the winning (daily) file: 2 owners there
    assert [o[2] for o in owners] == ["Example Widget Trading Co., Ltd.",
                                      "New Owner LLC"]
    sources = con.execute(
        f"SELECT DISTINCT file_name_source FROM read_parquet('"
        f"{(candidate / 'data' / 'owner').as_posix()}/*.parquet') "
        f"WHERE serial_no = '75930757'"
    ).fetchall()
    assert sources == [(DAILY_NEW,)]

    st = con.execute(
        f"SELECT statement_text FROM read_parquet('"
        f"{(candidate / 'data' / 'statement').as_posix()}/*.parquet') "
        f"WHERE serial_no = '75930757'"
    ).fetchall()
    assert st == [("Updated goods text",)]


def test_reconcile_release_prune(cfg, ledger):
    _stage_bronze(cfg, ledger)
    candidate = _run_pipeline(cfg, ledger)

    reconcile(candidate, ledger, log=lambda *a: None)
    assert (candidate / "profile" / "row_counts.csv").exists()
    assert (candidate / "profile" / "null_rates.csv").exists()

    release.swap(cfg, candidate)
    release.advance_watermarks(cfg, ledger)
    assert release.live_build(cfg) == candidate
    assert ledger.get_watermark("last_annual_integrated") == "2024-12-31"
    assert ledger.get_watermark("data_current_through") == "2025-03-01"
    assert ledger.get_watermark("last_daily_integrated") == DAILY_NEW

    n = release.prune_dailies(cfg, log=lambda *a: None)
    assert n == 1
    assert not (cfg.bronze_daily / DAILY_OLD).exists()
    assert (cfg.bronze_daily / DAILY_NEW).exists()  # still > cutoff


def test_reconcile_fails_on_unknown_lineage(cfg, ledger):
    _stage_bronze(cfg, ledger)
    candidate = _run_pipeline(cfg, ledger)
    ledger.db.execute("DELETE FROM downloads WHERE file_name = ?", (DAILY_NEW,))
    ledger.db.commit()
    with pytest.raises(ReconcileError, match="download"):
        reconcile(candidate, ledger, log=lambda *a: None)


def test_prune_refuses_without_reconciled_live(cfg, ledger):
    _stage_bronze(cfg, ledger)
    candidate = _run_pipeline(cfg, ledger)
    release.swap(cfg, candidate)  # swapped but never reconciled: no profile/
    with pytest.raises(RuntimeError, match="refusing to prune"):
        release.prune_dailies(cfg, log=lambda *a: None)
