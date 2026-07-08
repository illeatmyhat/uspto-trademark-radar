"""Download stage: decide which zips we need, enqueue ledger jobs, work them.

Work units are ``"{PRODUCT}/{fileName}"`` so a worker can reconstruct the
download URL and destination from the work unit alone — any number of
processes can run the stage concurrently against the same ledger.

What gets downloaded (ARCHITECTURE update job, ADR 0006):
- Annual: only the parts of the LATEST snapshot (even after a multi-year
  gap), skipping parts already in bronze/annual.
- Daily: every daily with transaction date > the latest annual's coverage
  cutoff that we don't already have. Dailies at or before the cutoff are
  superseded by the annual and never fetched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import PRODUCT_ANNUAL, PRODUCT_DAILY, Config
from .filenames import latest_annual_set, parse_daily
from .integrity import IntegrityError, assert_zip_valid
from .ledger import Ledger
from .odp import OdpClient

STAGE = "download"


@dataclass
class DownloadPlan:
    annual_cutoff: date
    new_annual: bool                      # cutoff newer than last integrated
    annual_files: list[str] = field(default_factory=list)  # still to fetch
    daily_files: list[str] = field(default_factory=list)   # still to fetch

    def summary(self) -> str:
        lines = [
            f"latest annual coverage cutoff : {self.annual_cutoff}",
            f"new annual to integrate       : {'yes' if self.new_annual else 'no'}",
            f"annual parts to download      : {len(self.annual_files)}",
            f"daily files to download       : {len(self.daily_files)}",
        ]
        return "\n".join(lines)


def _dest_for(cfg: Config, work_unit: str) -> tuple[str, str, Path]:
    """work_unit 'PRODUCT/name' -> (product, name, destination path)."""
    product, _, name = work_unit.partition("/")
    root = cfg.bronze_annual if product == PRODUCT_ANNUAL else cfg.bronze_daily
    return product, name, root / name


def plan(cfg: Config, ledger: Ledger, client: OdpClient) -> DownloadPlan:
    annual_manifest = client.list_zip_files(PRODUCT_ANNUAL)
    picked = latest_annual_set(list(annual_manifest))
    assert picked is not None, "preflight guarantees a parseable annual set"
    cutoff, parts = picked

    last = ledger.get_watermark("last_annual_integrated")
    new_annual = last is None or date.fromisoformat(last) < cutoff

    plan = DownloadPlan(annual_cutoff=cutoff, new_annual=new_annual)

    for a in parts:
        if not (cfg.bronze_annual / a.name).exists():
            plan.annual_files.append(a.name)

    daily_manifest = client.list_zip_files(PRODUCT_DAILY)
    for name in sorted(daily_manifest):
        d = parse_daily(name)
        if d and d.transaction_dt > cutoff \
                and not (cfg.bronze_daily / name).exists():
            plan.daily_files.append(name)

    for name in plan.annual_files:
        ledger.ensure_job(f"{PRODUCT_ANNUAL}/{name}", STAGE)
    for name in plan.daily_files:
        ledger.ensure_job(f"{PRODUCT_DAILY}/{name}", STAGE)
    return plan


def work(cfg: Config, ledger: Ledger, client: OdpClient,
         log=print) -> tuple[int, int]:
    """Claim-and-download until no claimable jobs remain.
    Returns (done, failed) counts for this worker.
    """
    cfg.ensure_dirs()
    # Official byte counts from the product manifests — the only integrity
    # metadata USPTO publishes (no checksums as of 2026-07).
    official_size: dict[str, int | None] = {}
    for product in (PRODUCT_ANNUAL, PRODUCT_DAILY):
        for rf in client.list_zip_files(product).values():
            official_size[f"{product}/{rf.name}"] = rf.size

    done = failed = 0
    while (job := ledger.claim(STAGE)) is not None:
        product, name, dest = _dest_for(cfg, job.work_unit)
        url = f"{client.base}/files/{product}/{name}"
        log(f"[download] {job.work_unit} (attempt {job.attempt_count})")
        try:
            size, sha256, etag = client.download(
                url, dest, heartbeat=lambda j=job: ledger.heartbeat(j.id)
            )
            expected = official_size.get(job.work_unit)
            if expected is not None and size != expected:
                raise IntegrityError(
                    f"{name}: {size} bytes on disk, but the USPTO manifest "
                    f"says {expected}"
                )
            ledger.heartbeat(job.id)  # CRC sweep of a multi-GB zip takes a while
            assert_zip_valid(dest)
        except IntegrityError as e:
            # Corrupt archive: remove it so the retry re-downloads instead
            # of "skipping (exists)" over the bad bytes forever.
            dest.unlink(missing_ok=True)
            ledger.fail(job.id, f"IntegrityError: {e}")
            failed += 1
            log(f"  FAILED (corrupt, deleted): {e}")
        except Exception as e:  # noqa: BLE001 — ledger records, triage later
            ledger.fail(job.id, f"{type(e).__name__}: {e}")
            failed += 1
            log(f"  FAILED: {e}")
        else:
            ledger.record_download(name, product, url, size, sha256, etag)
            ledger.complete(job.id)
            done += 1
            log(f"  ok ({size >> 20} MiB, sha256 {sha256[:12]}...)")
    return done, failed
