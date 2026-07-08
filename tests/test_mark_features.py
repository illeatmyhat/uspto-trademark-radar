import json

import duckdb

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.gold import mark_features
from uspto_trademark_radar.integrity import sha256_file


def test_mark_features_builds_and_scores(cfg, ledger):
    cases = [
        make_case_file("70000001", mark="QVOWII", owner_country="CN"),
        make_case_file("70000002", mark="APPLE VALLEY", owner_country="US"),
    ]
    z = make_zip(cfg.bronze_annual / "apc18840407-20241231-01.zip", make_xml(cases))
    ledger.record_download("apc18840407-20241231-01.zip", "TRTYRAP", "http://t",
                           z.stat().st_size, sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    candidate = build_candidate(cfg, cutoff, zips)

    out = mark_features.build(candidate, cfg.data_root / "gold", log=lambda *a: None)
    con = duckdb.connect()
    rows = dict(con.execute(
        f"SELECT mark_id_char, single_token FROM read_parquet('{out.as_posix()}/*.parquet')"
    ).fetchall())
    assert set(rows) == {"QVOWII", "APPLE VALLEY"}
    assert rows["QVOWII"] is True            # single token
    assert rows["APPLE VALLEY"] is False     # two tokens

    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{out.as_posix()}/*.parquet')").fetchall()]
    for expected in ["serial_no", "coined_mark_score", "coined_token_max",
                     "is_marketplace_class", "filing_basis", "owner_country",
                     "goods_item_count", "final_refusal_in",
                     "abandoned_no_use_in"]:
        assert expected in cols

    meta = json.loads((out / "_lineage.json").read_text())
    assert meta["version"] == "1" and meta["n_marks"] == 2
    assert "publishable" in meta["scope"]

    # rebuild over the existing output must replace it, not fail on the
    # non-empty COPY target
    out2 = mark_features.build(candidate, cfg.data_root / "gold",
                               log=lambda *a: None)
    n = con.execute(
        f"SELECT count(*) FROM read_parquet('{out2.as_posix()}/*.parquet')"
    ).fetchone()
    assert n is not None and n[0] == 2
