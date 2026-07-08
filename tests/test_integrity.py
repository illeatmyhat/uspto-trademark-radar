import hashlib
import sqlite3

import pytest

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.integrity import (
    IntegrityError,
    assert_sha256,
    assert_zip_valid,
    sha256_file,
)
from uspto_trademark_radar.ledger import Ledger


def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello bronze")
    assert sha256_file(p) == hashlib.sha256(b"hello bronze").hexdigest()


def test_zip_crc_detects_corruption(tmp_path):
    z = make_zip(tmp_path / "ok.zip", make_xml([make_case_file("111")]))
    assert_zip_valid(z)  # sane zip passes

    raw = bytearray(z.read_bytes())
    mid = len(raw) // 2
    raw[mid] ^= 0xFF  # flip bits inside the deflate stream
    bad = tmp_path / "bad.zip"
    bad.write_bytes(bytes(raw))
    with pytest.raises(IntegrityError):
        assert_zip_valid(bad)

    notzip = tmp_path / "notzip.zip"
    notzip.write_bytes(b"PK\x00\x00 nope")
    with pytest.raises(IntegrityError, match="not a valid zip"):
        assert_zip_valid(notzip)


def test_assert_sha256_mismatch(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"data")
    assert_sha256(p, sha256_file(p))  # match passes
    with pytest.raises(IntegrityError, match="does not match the ledger"):
        assert_sha256(p, "0" * 64)


def test_parse_stage_rejects_tampered_bronze(cfg, ledger):
    name = "apc18840407-20241231-01.zip"
    z = make_zip(cfg.bronze_annual / name,
                 make_xml([make_case_file("75930757")]))
    ledger.record_download(name, "TRTYRAP", "http://test",
                           z.stat().st_size, sha256_file(z))
    z.write_bytes(make_zip(cfg.bronze_annual / "tmp.zip",
                           make_xml([make_case_file("999")])).read_bytes())
    (cfg.bronze_annual / "tmp.zip").unlink()

    parse_stage.plan(cfg, ledger)
    done, failed = parse_stage.work(cfg, ledger, log=lambda *a: None)
    assert (done, failed) == (0, 1)
    (unit, status, error), = [r for r in ledger.pending_or_failed("parse")]
    assert unit == name and status == "failed"
    assert "does not match the ledger" in error


def test_ledger_sha256_migration(tmp_path):
    """A ledger created before the sha256 column gains it on open."""
    db_path = tmp_path / "old.sqlite"
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE downloads (file_name TEXT PRIMARY KEY, product TEXT "
        "NOT NULL, url TEXT, size_bytes INTEGER, downloaded_at TEXT NOT NULL)"
    )
    con.execute("INSERT INTO downloads VALUES ('a.zip', 'TRTYRAP', 'u', 1, 'ts')")
    con.commit()
    con.close()

    led = Ledger(db_path)
    rec = led.get_download("a.zip")
    assert rec == {"file_name": "a.zip", "product": "TRTYRAP", "url": "u",
                   "size_bytes": 1, "sha256": None, "etag": None,
                   "downloaded_at": "ts"}
    led.record_download("b.zip", "TRTDXFAP", "u2", 2, "f" * 64, "e" * 32)
    rec = led.get_download("b.zip")
    assert rec is not None
    assert rec["sha256"] == "f" * 64 and rec["etag"] == "e" * 32
    led.close()
