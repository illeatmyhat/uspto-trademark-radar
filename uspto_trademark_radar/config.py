"""Central configuration. Paths are relative to the repo root by default and
overridable via TRADEMARK_RADAR_DATA_ROOT so the code repo and the (large) data tree
can live on different volumes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Source products (ADR 0001 — these two and nothing else).
PRODUCT_ANNUAL = "TRTYRAP"
PRODUCT_DAILY = "TRTDXFAP"

API_BASE = "https://api.uspto.gov/api/v1/datasets/products"

# Bump when parser output changes shape/semantics; silver_parts are keyed by
# this, so a bump invalidates the parse cache and forces a re-parse.
PARSER_VERSION = "1"

# DTD versions the parser is known to handle. Anything else is a hard stop
# (silently mis-parsed silver is worse than a failed build).
SUPPORTED_DTD_VERSIONS = {"2.0"}


def default_workers(stage: str) -> int:
    """Stages parallelize across processes by default. Parse is CPU-bound
    and single-threaded per zip, so use nearly all cores; downloads are
    network-bound and capped low out of politeness to USPTO.
    """
    n = os.cpu_count() or 4
    if stage == "download":
        return min(8, n)
    return max(1, n - 2)


@dataclass
class Config:
    repo_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1]
    )
    # NB: Path("") normalizes to "." and every Path is truthy, so a
    # `Path(env) or default` fallback silently roots the data tree in the
    # CWD — check the env var itself.
    data_root: Path = field(
        default_factory=lambda: (
            Path(os.environ["TRADEMARK_RADAR_DATA_ROOT"])
            if os.environ.get("TRADEMARK_RADAR_DATA_ROOT")
            else Path(__file__).resolve().parents[1] / "data"
        )
    )

    @property
    def bronze_annual(self) -> Path:
        return self.data_root / "bronze" / "annual"

    @property
    def bronze_daily(self) -> Path:
        return self.data_root / "bronze" / "daily"

    @property
    def silver_parts(self) -> Path:
        return self.data_root / "silver_parts" / f"v{PARSER_VERSION}"

    @property
    def builds(self) -> Path:
        return self.data_root / "builds"

    @property
    def live_pointer(self) -> Path:
        # Plain JSON pointer file rather than a symlink: atomic to flip via
        # os.replace and needs no privileges on Windows.
        return self.data_root / "builds" / "LIVE.json"

    @property
    def ledger_path(self) -> Path:
        return self.data_root / "ledger.sqlite"

    def api_key(self) -> str:
        key = os.environ.get("USPTO_API_KEY")
        if not key:
            raise SystemExit(
                "USPTO_API_KEY is not set. Keys are personal (USPTO.gov "
                "account + ID.me) — see RUNBOOK.md §1 for minting a new one."
            )
        return key

    def ensure_dirs(self) -> None:
        for p in (self.bronze_annual, self.bronze_daily,
                  self.silver_parts, self.builds):
            p.mkdir(parents=True, exist_ok=True)
