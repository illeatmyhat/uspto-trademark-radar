"""Publish gates (schema §5) + data profile emission.

Gate failure means: no pointer swap, no daily prune, no publish. The
candidate build stays on disk for triage (RUNBOOK §3).

Gates:
1. `case_file.serial_no` unique.
2. Referential integrity: every child serial_no exists in case_file.
3. Date sanity — calibrated against the real corpus (2026-07 first build):
   per-column ceilings by semantics (scheduled publications/registrations
   run a few weeks ahead; expirations/renewals run ~10 years ahead), floor
   1870 for all, and the gate FAILS only when outliers exceed 0.1% of a
   table — the level a systematic parse bug (e.g. misread date format)
   would instantly blow through. Sub-threshold outliers are source dirt
   (year-1000 first-use claims etc.): documented in
   profile/date_outliers.csv, per the "document, don't clean" policy.
4. Volume: case_file count not lower than the previous live build's
   (the corpus only grows; a shrink means input loss). The absolute
   cross-check against OCE/USPTO published statistics is a manual RUNBOOK
   step until a reference file is added (see docs/silver-schema.md §5).
5. Lineage: every file_name_source appears in the ledger's downloads table.

Also writes profile/ (null_rates.csv, row_counts.csv, build_lineage.json)
into the candidate — published alongside the data.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa

from .build import read_meta
from .db import one_row, scalar
from .ledger import Ledger
from .schema import TABLES

DATE_FLOOR = date(1870, 1, 1)

# Columns that legitimately hold future dates. LONG: expirations/renewal
# horizons (10-year terms, observed max ~today+10y; ceiling generous).
# SHORT: scheduled administrative actions (Official Gazette runs weeks ahead).
FUTURE_OK_LONG = {"renewal_dt", "for_reg_exp_dt", "for_renewal_dt",
                  "for_renewal_exp_dt", "ir_renewal_dt", "ir_death_dt",
                  "ir_auto_reg_dt", "irregularity_reply_by_dt"}
FUTURE_OK_SHORT = {"publication_dt", "registration_dt", "ir_publication_dt",
                   "ir_registration_dt"}
DATE_OUTLIER_TOLERANCE = 0.001  # fraction of table rows


class ReconcileError(RuntimeError):
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__(
            "reconciliation FAILED — build not released:\n  - "
            + "\n  - ".join(failures)
        )


def _glob(build_dir: Path, table: str) -> str:
    return (build_dir / "data" / table).as_posix() + "/*.parquet"


def _has_files(build_dir: Path, table: str) -> bool:
    return any((build_dir / "data" / table).glob("*.parquet"))


def reconcile(build_dir: Path, ledger: Ledger,
              previous_build: Path | None = None,
              log=print) -> None:
    """Run all gates; raise ReconcileError on any failure."""
    con = duckdb.connect()
    failures: list[str] = []
    meta = read_meta(build_dir)

    if not _has_files(build_dir, "case_file"):
        raise ReconcileError(["case_file has no parquet output at all"])
    cf = _glob(build_dir, "case_file")

    # 1. serial uniqueness
    dupes = scalar(
        con,
        f"SELECT count(*) FROM (SELECT serial_no FROM read_parquet('{cf}') "
        f"GROUP BY serial_no HAVING count(*) > 1)",
    )
    if dupes:
        failures.append(f"case_file: {dupes} duplicated serial_no values")

    # 2. referential integrity
    for table in TABLES:
        if table == "case_file" or not _has_files(build_dir, table):
            continue
        orphans = scalar(
            con,
            f"SELECT count(*) FROM read_parquet('{_glob(build_dir, table)}') t "
            f"WHERE NOT EXISTS (SELECT 1 FROM read_parquet('{cf}') c "
            f"WHERE c.serial_no = t.serial_no)",
        )
        if orphans:
            failures.append(f"{table}: {orphans} rows with serial_no "
                            "missing from case_file")

    # 3. date sanity (see module docstring for the calibration rationale)
    date_outliers: list[tuple] = []  # (table, column, below_floor, above_ceiling, ceiling)
    for table, schema in TABLES.items():
        if not _has_files(build_dir, table):
            continue
        glob = _glob(build_dir, table)
        total = scalar(con, f"SELECT count(*) FROM read_parquet('{glob}')")
        date_cols = [f.name for f in schema if f.type == pa.date32()
                     and f.name != "file_dt_source"]
        for col in date_cols:
            if col in FUTURE_OK_LONG:
                ceiling = "current_date + INTERVAL 40 YEAR"
            elif col in FUTURE_OK_SHORT:
                ceiling = "current_date + INTERVAL 120 DAY"
            else:
                ceiling = "current_date"
            below, above = one_row(
                con,
                f"SELECT count(*) FILTER ({col} < DATE '{DATE_FLOOR}'), "
                f"count(*) FILTER ({col} > {ceiling}) "
                f"FROM read_parquet('{glob}')",
            )
            if below or above:
                date_outliers.append((table, col, below, above, ceiling))
            if total and (below + above) / total > DATE_OUTLIER_TOLERANCE:
                failures.append(
                    f"{table}.{col}: {below + above} dates outside policy "
                    f"([{DATE_FLOOR}, {ceiling}]) — "
                    f"{(below + above) / total:.2%} of rows exceeds the "
                    f"{DATE_OUTLIER_TOLERANCE:.1%} tolerance; suspect a "
                    "systematic parse bug, not source dirt"
                )

    # 4. volume vs previous live build
    n_cases = scalar(con, f"SELECT count(*) FROM read_parquet('{cf}')")
    if previous_build is not None and _has_files(previous_build, "case_file"):
        prev = scalar(
            con,
            f"SELECT count(*) FROM "
            f"read_parquet('{_glob(previous_build, 'case_file')}')",
        )
        if n_cases < prev:
            failures.append(
                f"case_file shrank: {n_cases:,} vs {prev:,} in previous "
                "build — input files are likely missing"
            )
    log(f"[reconcile] case_file rows: {n_cases:,}")

    # 5. lineage: every source file has a download record
    known = ledger.downloaded_files()
    sources = {r[0] for r in con.execute(
        f"SELECT DISTINCT file_name_source FROM read_parquet('{cf}')"
    ).fetchall()}
    unknown = sources - known
    if unknown:
        failures.append(
            f"{len(unknown)} file_name_source values have no download "
            f"record in the ledger (e.g. {sorted(unknown)[0]})"
        )

    _write_profile(con, build_dir, meta, date_outliers)

    if failures:
        raise ReconcileError(failures)
    log("[reconcile] all gates passed")


def _write_profile(con: duckdb.DuckDBPyConnection, build_dir: Path,
                   meta: dict, date_outliers: list[tuple]) -> None:
    profile = build_dir / "profile"
    profile.mkdir(exist_ok=True)

    with open(profile / "date_outliers.csv", "w", encoding="utf-8") as f:
        f.write("table,column,below_1870,above_ceiling,ceiling_policy\n")
        for table, col, below, above, ceiling in date_outliers:
            f.write(f"{table},{col},{below},{above},{ceiling}\n")

    with open(profile / "row_counts.csv", "w", encoding="utf-8") as f:
        f.write("table,rows\n")
        for table in TABLES:
            n = 0
            if _has_files(build_dir, table):
                n = scalar(
                    con,
                    f"SELECT count(*) FROM read_parquet('{_glob(build_dir, table)}')",
                )
            f.write(f"{table},{n}\n")

    with open(profile / "null_rates.csv", "w", encoding="utf-8") as f:
        f.write("table,column,null_rate\n")
        for table, schema in TABLES.items():
            if not _has_files(build_dir, table):
                continue
            cols = ", ".join(
                f"avg(CASE WHEN {c.name} IS NULL THEN 1.0 ELSE 0.0 END) AS {c.name}"
                for c in schema
            )
            row = one_row(
                con,
                f"SELECT {cols} FROM read_parquet('{_glob(build_dir, table)}')",
            )
            for c, rate in zip(schema.names, row, strict=True):
                f.write(f"{table},{c},{0.0 if rate is None else round(rate, 6)}\n")

    (profile / "build_lineage.json").write_text(json.dumps(meta, indent=2))
