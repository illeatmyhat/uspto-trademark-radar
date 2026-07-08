"""Release: atomic pointer flip to a reconciled build, then (only then)
prune superseded dailies and advance watermarks (ARCHITECTURE update job).

The "pointer" is builds/LIVE.json (os.replace is atomic; no symlink
privileges needed on Windows). Anything already reading the old build keeps
its directory — builds are immutable once created.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path

from .build import read_meta
from .config import Config
from .filenames import parse_daily
from .ledger import Ledger


def live_build(cfg: Config) -> Path | None:
    if not cfg.live_pointer.exists():
        return None
    name = json.loads(cfg.live_pointer.read_text())["build"]
    p = cfg.builds / name
    return p if p.exists() else None


def swap(cfg: Config, candidate: Path) -> None:
    """Atomically point LIVE.json at `candidate`."""
    tmp = cfg.live_pointer.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"build": candidate.name}))
    os.replace(tmp, cfg.live_pointer)


def advance_watermarks(cfg: Config, ledger: Ledger) -> None:
    live = live_build(cfg)
    if live is None:
        raise RuntimeError("no live build; swap before advancing watermarks")
    meta = read_meta(live)
    ledger.set_watermark("last_annual_integrated", meta["annual_cutoff"])
    ledger.set_watermark("data_current_through", meta["data_current_through"])
    dailies = [n for n in meta["inputs"] if parse_daily(n)]
    if dailies:
        ledger.set_watermark("last_daily_integrated", max(dailies))


def prune_dailies(cfg: Config, log=print) -> int:
    """Delete bronze dailies covered by the live build's annual cutoff.
    LAST step, gated: refuses to run unless a live build exists and its
    reconciliation profile was written (i.e. gates ran).
    """
    live = live_build(cfg)
    if live is None or not (live / "profile" / "build_lineage.json").exists():
        raise RuntimeError(
            "refusing to prune: no reconciled live build (gated prune)"
        )
    cutoff = date.fromisoformat(read_meta(live)["annual_cutoff"])
    n = 0
    for p in sorted(cfg.bronze_daily.glob("*.zip")):
        d = parse_daily(p.name)
        if d and d.transaction_dt <= cutoff:
            p.unlink()
            n += 1
            log(f"[prune] {p.name} (<= {cutoff})")
    return n


def prune_builds(cfg: Config, keep: int = 2, log=print) -> int:
    """Optional housekeeping: drop old immutable builds, never the live one."""
    live = live_build(cfg)
    builds = sorted(
        (p for p in cfg.builds.glob("build-*") if p.is_dir()),
        key=lambda p: p.name,
    )
    doomed = [b for b in builds[:-keep] if live is None or b != live]
    for b in doomed:
        shutil.rmtree(b)
        log(f"[prune] build {b.name}")
    return len(doomed)
