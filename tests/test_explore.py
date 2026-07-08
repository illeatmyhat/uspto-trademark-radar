from datetime import date

import duckdb

from test_build_reconcile import _run_pipeline, _stage_bronze
from uspto_trademark_radar.db import scalar
from uspto_trademark_radar.explore import make_views_db


def test_views_db_over_build(cfg, ledger):
    _stage_bronze(cfg, ledger)
    candidate = _run_pipeline(cfg, ledger)

    db = cfg.data_root / "silver.duckdb"
    views = make_views_db(candidate, db)
    assert "case_file" in views and "owner" in views
    assert "lookup_status_codes" in views

    con = duckdb.connect(str(db), read_only=True)
    assert scalar(con, "SELECT count(*) FROM case_file") == 2
    mark = scalar(
        con, "SELECT mark_id_char FROM case_file WHERE serial_no = '75930757'"
    )
    assert mark == "VOFULY-2"  # views see the merged latest-wins state
    assert scalar(con, "SELECT annual_cutoff FROM _build") == date(2024, 12, 31)
    assert scalar(
        con, "SELECT description FROM lookup_status_codes WHERE code = '700'"
    ) == "REGISTERED"
    con.close()

    # re-running against the same db must repoint cleanly (CREATE OR REPLACE)
    views2 = make_views_db(candidate, db)
    assert views2 == views
