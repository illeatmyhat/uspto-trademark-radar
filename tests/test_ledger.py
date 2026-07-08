import time


def test_claim_lifecycle(ledger):
    ledger.ensure_job("a.zip", "download")
    ledger.ensure_job("a.zip", "download")  # idempotent
    ledger.ensure_job("b.zip", "download")

    j1 = ledger.claim("download")
    assert j1 and j1.work_unit == "a.zip" and j1.attempt_count == 1
    j2 = ledger.claim("download")
    assert j2 and j2.work_unit == "b.zip"
    assert ledger.claim("download") is None  # both leased

    ledger.complete(j1.id)
    ledger.fail(j2.id, "boom")
    assert ledger.is_done("a.zip", "download")
    assert not ledger.is_done("b.zip", "download")
    assert ledger.claim("download") is None  # done/failed not claimable

    assert ledger.retry_failed("download") == 1
    j3 = ledger.claim("download")
    assert j3 and j3.work_unit == "b.zip" and j3.attempt_count == 2


def test_requeue_reopens_done_but_not_running(ledger):
    ledger.ensure_job("a.zip", "parse")
    ledger.ensure_job("b.zip", "parse")
    ja = ledger.claim("parse")
    jb = ledger.claim("parse")
    ledger.complete(ja.id)                       # a done; b still running

    ledger.requeue("a.zip", "parse")
    ledger.requeue("b.zip", "parse")             # must NOT steal the lease
    j = ledger.claim("parse")
    assert j and j.work_unit == "a.zip" and j.attempt_count == 2
    assert ledger.claim("parse") is None         # b stays with its worker
    ledger.complete(j.id)
    ledger.complete(jb.id)


def test_expired_lease_is_reclaimable(ledger):
    ledger.ensure_job("a.zip", "parse")
    j1 = ledger.claim("parse", lease_seconds=0)
    assert j1 is not None
    time.sleep(0.05)  # let the zero-length lease expire
    j2 = ledger.claim("parse")
    assert j2 is not None and j2.id == j1.id and j2.attempt_count == 2


def test_heartbeat_extends_lease(ledger):
    ledger.ensure_job("a.zip", "parse")
    j = ledger.claim("parse", lease_seconds=0)
    ledger.heartbeat(j.id, lease_seconds=3600)
    time.sleep(0.05)
    assert ledger.claim("parse") is None  # lease renewed, not claimable


def test_watermarks_and_downloads(ledger):
    assert ledger.get_watermark("last_annual_integrated") is None
    ledger.set_watermark("last_annual_integrated", "2024-12-31")
    ledger.set_watermark("last_annual_integrated", "2025-12-31")
    assert ledger.get_watermark("last_annual_integrated") == "2025-12-31"

    ledger.record_download("apc250101.zip", "TRTDXFAP", "http://x", 123)
    assert ledger.downloaded_files() == {"apc250101.zip"}
    assert ledger.downloaded_files("TRTYRAP") == set()
