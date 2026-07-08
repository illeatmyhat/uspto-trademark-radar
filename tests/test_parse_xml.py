from datetime import date

import duckdb
import pytest

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar.parse_xml import DtdVersionError, parse_zip


def _parse(tmp_path, xml, name="apc250101.zip"):
    z = make_zip(tmp_path / "bronze" / name, xml)
    return parse_zip(z, tmp_path / "parts", date(2025, 1, 1)), tmp_path / "parts" / z.stem


def test_parse_basic(tmp_path):
    xml = make_xml([
        make_case_file("75930757", mark="VOFULY"),
        make_case_file("1234", mark="BLORPTEK", owner_country="US"),
    ])
    manifest, out = _parse(tmp_path, xml)
    assert manifest["case_file_count"] == 2
    assert manifest["dtd_versions"] == ["2.0"]
    assert manifest["row_counts"]["owner"] == 2
    assert manifest["row_counts"]["event"] == 4

    con = duckdb.connect()
    rows = con.execute(
        f"SELECT serial_no, mark_id_char, use_af_in, use_intent_in, "
        f"file_name_source FROM read_parquet('{(out / 'case_file.parquet').as_posix()}') "
        f"ORDER BY serial_no"
    ).fetchall()
    assert rows[0][0] == "00001234"  # zero-padded serial
    assert rows[1] == ("75930757", "VOFULY", True, False, "apc250101.zip")

    wide = con.execute(
        f"SELECT use_file_in, use_intent_file_in, opposition_pend_in, "
        f"use_afdv_file_in, draw_color_cur_in, file_location, "
        f"domestic_rep_name, cancel_cd, ir_no, ir_status_cd, "
        f"ir_first_refus_in, related_other_in FROM "
        f"read_parquet('{(out / 'case_file.parquet').as_posix()}') "
        f"WHERE serial_no = '75930757'"
    ).fetchone()
    assert wide == (True, False, False, False, False,
                    "PUBLICATION AND ISSUE SECTION", "Example Rep LLC", None,
                    "1234567", "400", False, True)

    pm = con.execute(
        f"SELECT prior_no, prior_type_cd FROM "
        f"read_parquet('{(out / 'prior_mark.parquet').as_posix()}') LIMIT 1"
    ).fetchone()
    assert pm == ("75000001", "0")

    ic = con.execute(
        f"SELECT intl_class_cd FROM read_parquet('{(out / 'intl_class.parquet').as_posix()}')"
    ).fetchall()
    assert all(c == ("009",) for c in ic)  # zero-padded class code

    st = con.execute(
        f"SELECT statement_type_cd, statement_text FROM "
        f"read_parquet('{(out / 'statement.parquet').as_posix()}') LIMIT 1"
    ).fetchone()
    assert st == ("GS0091", "Cell phone cases")


def test_parse_cache_hit(tmp_path):
    xml = make_xml([make_case_file("111")])
    m1, out = _parse(tmp_path, xml)
    stamp = (out / "_manifest.json").stat().st_mtime_ns
    m2, _ = _parse(tmp_path, xml)
    assert m2 == m1
    assert (out / "_manifest.json").stat().st_mtime_ns == stamp  # not rebuilt


def test_unknown_dtd_version_hard_stops(tmp_path):
    xml = make_xml([make_case_file("111")], version="3.1")
    with pytest.raises(DtdVersionError, match="3.1"):
        _parse(tmp_path, xml)


def test_missing_version_hard_stops(tmp_path):
    xml = make_xml([make_case_file("111")]).replace(
        b"<version><version-no>2.0</version-no>"
        b"<version-date>20061020</version-date></version>", b"")
    with pytest.raises(DtdVersionError, match="version-no"):
        _parse(tmp_path, xml)


def test_incomplete_output_is_rebuilt(tmp_path):
    xml = make_xml([make_case_file("111")])
    manifest, out = _parse(tmp_path, xml)
    (out / "_manifest.json").unlink()  # simulate crash mid-parse
    m2, _ = _parse(tmp_path, xml)
    assert m2["case_file_count"] == 1
    assert (out / "_manifest.json").exists()
