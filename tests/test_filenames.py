from datetime import date

from uspto_trademark_radar.filenames import (
    file_dt_source,
    latest_annual_set,
    parse_annual,
    parse_daily,
)


def test_parse_annual():
    a = parse_annual("apc18840407-20241231-03.zip")
    assert a and a.start == date(1884, 4, 7)
    assert a.cutoff == date(2024, 12, 31)
    assert a.part == 3
    assert parse_annual("apc250630.zip") is None
    assert parse_annual("tmog-20240101.zip") is None


def test_parse_daily():
    d = parse_daily("apc250630.zip")
    assert d and d.transaction_dt == date(2025, 6, 30)
    assert parse_daily("apc18840407-20241231-03.zip") is None
    assert parse_daily("apc251340.zip") is None  # month 13


def test_file_dt_source_uses_cutoff_not_start():
    assert file_dt_source("apc18840407-20241231-01.zip") == date(2024, 12, 31)
    assert file_dt_source("apc250102.zip") == date(2025, 1, 2)
    assert file_dt_source("unrecognized.zip") is None


def test_latest_annual_set_picks_newest_snapshot_only():
    names = [
        "apc18840407-20231231-01.zip",
        "apc18840407-20231231-02.zip",
        "apc18840407-20241231-02.zip",
        "apc18840407-20241231-01.zip",
        "readme.txt",
    ]
    picked = latest_annual_set(names)
    assert picked is not None
    cutoff, parts = picked
    assert cutoff == date(2024, 12, 31)
    assert [p.part for p in parts] == [1, 2]
    assert latest_annual_set(["nope.zip"]) is None
