"""Restore bronze from the HF mirror — the enabler for stateless runners.

A cloud job starts with an empty disk. Instead of pulling the 17+ GB
backfile from USPTO (personal API key, rate limits, slower), it restores
bronze from our own append-only HF mirror at datacenter speed, then hits
USPTO only for files the mirror doesn't have yet (the download stage does
that as usual).

Restored files get proper `downloads` ledger records (sha256 included) so
the parse stage's integrity verification and the reconcile lineage gate
hold exactly as they do for direct USPTO downloads.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import PurePosixPath

from huggingface_hub import HfApi, hf_hub_download

from .config import PRODUCT_ANNUAL, PRODUCT_DAILY, Config
from .filenames import latest_annual_set, parse_daily
from .integrity import sha256_file
from .ledger import Ledger


def plan_restore(repo_files: list[str]) -> tuple[date, list[str]]:
    """From the mirror's file listing, pick what a fresh build needs:
    every part of the LATEST annual snapshot + dailies after its cutoff.
    (The mirror is append-only and holds older editions too — skip those.)
    """
    annual_names = [PurePosixPath(f).name for f in repo_files
                    if f.startswith("annual/")]
    picked = latest_annual_set(annual_names)
    if picked is None:
        raise SystemExit(
            "bronze mirror contains no parseable annual snapshot — "
            "run a local publish --bronze-repo first"
        )
    cutoff, parts = picked
    wanted = [f"annual/{a.name}" for a in parts]
    for f in sorted(repo_files):
        if f.startswith("daily/"):
            d = parse_daily(PurePosixPath(f).name)
            if d and d.transaction_dt > cutoff:
                wanted.append(f)
    return cutoff, wanted


def restore_bronze(cfg: Config, ledger: Ledger, mirror_repo: str,
                   log=print) -> int:
    """Download missing bronze files from the mirror; record lineage.
    Idempotent: files already on disk are only (re-)recorded, not fetched.
    Returns the number of files downloaded.
    """
    cfg.ensure_dirs()
    token = os.environ.get("HF_TOKEN")  # mirror is public; token optional
    api = HfApi(token=token)
    files = api.list_repo_files(mirror_repo, repo_type="dataset")
    cutoff, wanted = plan_restore(files)
    log(f"[restore] mirror {mirror_repo}: latest snapshot cutoff {cutoff}, "
        f"{len(wanted)} files in scope")

    known = ledger.downloaded_files()
    fetched = 0
    for rf in wanted:
        name = PurePosixPath(rf).name
        dest_dir = (cfg.bronze_annual if rf.startswith("annual/")
                    else cfg.bronze_daily)
        dest = dest_dir / name
        if not dest.exists():
            hf_hub_download(mirror_repo, rf, repo_type="dataset",
                            local_dir=cfg.data_root / "bronze", token=token)
            fetched += 1
            log(f"[restore] {rf}")
        if name not in known:
            product = (PRODUCT_ANNUAL if rf.startswith("annual/")
                       else PRODUCT_DAILY)
            ledger.record_download(
                name, product,
                f"hf://datasets/{mirror_repo}/{rf}",
                dest.stat().st_size, sha256_file(dest),
            )
    log(f"[restore] done: {fetched} downloaded, "
        f"{len(wanted) - fetched} already present")
    return fetched
