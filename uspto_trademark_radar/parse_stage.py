"""Parse stage: ledger-driven worker loop around parse_xml.parse_zip.

The input set for a build is always: the latest annual snapshot present in
bronze/annual + every daily in bronze/daily with transaction date after that
snapshot's coverage cutoff. Anything else in bronze is ignored (stale dailies
are pruned by the release stage, older annual snapshots are kept as archive).

parse_zip itself is a parse-once cache keyed by parser version, so re-running
this stage after a crash — or after a PARSER_VERSION bump — does exactly the
minimum work.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .config import Config
from .filenames import file_dt_source, latest_annual_set, parse_daily
from .integrity import assert_sha256
from .ledger import Ledger
from .parse_xml import parse_zip

STAGE = "parse"


def input_zips(cfg: Config) -> tuple[date, list[Path]]:
    """(annual coverage cutoff, ordered zips that make up the next build)."""
    annual_names = [p.name for p in cfg.bronze_annual.glob("*.zip")]
    picked = latest_annual_set(annual_names)
    if picked is None:
        raise SystemExit(
            "no annual snapshot in bronze/annual — run the download stage first"
        )
    cutoff, parts = picked
    zips = [cfg.bronze_annual / a.name for a in parts]
    for p in sorted(cfg.bronze_daily.glob("*.zip")):
        d = parse_daily(p.name)
        if d and d.transaction_dt > cutoff:
            zips.append(p)
    return cutoff, zips


def plan(cfg: Config, ledger: Ledger) -> list[Path]:
    _, zips = input_zips(cfg)
    todo = []
    for z in zips:
        ledger.ensure_job(z.name, STAGE)
        # The manifest is the ground truth for parse-once; a job whose output
        # vanished (deleted cache, parser-version bump) must re-queue or the
        # ledger would consider it done forever.
        if not (cfg.silver_parts / z.stem / "_manifest.json").exists():
            todo.append(z)
            ledger.requeue(z.name, STAGE)
    return todo


def work(cfg: Config, ledger: Ledger, log=print) -> tuple[int, int]:
    cfg.ensure_dirs()
    _, zips = input_zips(cfg)
    by_name = {z.name: z for z in zips}
    done = failed = 0
    # Annual parts are multi-GB; hours-long parses are normal. Lease
    # generously rather than plumbing heartbeats through iterparse.
    while (job := ledger.claim(STAGE, lease_seconds=6 * 3600)) is not None:
        path = by_name.get(job.work_unit)
        log(f"[parse] {job.work_unit} (attempt {job.attempt_count})")
        try:
            if path is None:
                raise FileNotFoundError(
                    f"{job.work_unit} is not part of the current input set "
                    "(stale job?) — re-run the download stage or retry_failed"
                )
            dt = file_dt_source(path.name)
            assert dt is not None  # input_zips only yields parseable names
            # Verify bronze against the hash recorded at download time —
            # catches at-rest corruption years later. Files predating hash
            # recording (no sha256 in the ledger) parse unverified.
            rec = ledger.get_download(path.name)
            if rec and rec.get("sha256"):
                assert_sha256(path, rec["sha256"])
            manifest = parse_zip(path, cfg.silver_parts, dt)
        except Exception as e:  # noqa: BLE001 — ledger records, triage later
            ledger.fail(job.id, f"{type(e).__name__}: {e}")
            failed += 1
            log(f"  FAILED: {e}")
        else:
            ledger.complete(job.id)
            done += 1
            log(f"  ok ({manifest['case_file_count']:,} case files)")
    return done, failed
