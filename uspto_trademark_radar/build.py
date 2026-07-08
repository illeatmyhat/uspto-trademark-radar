"""Candidate build: DuckDB latest-wins merge over parsed parts.

Rather than mutating live silver in place, every build materializes a fresh
candidate directory from the parse cache (ARCHITECTURE update job — never in place):

- "winners": for each serial_no, the source file with the newest
  file_dt_source among the input set (annual parts + post-cutoff dailies).
  This IS the daily replay (ARCHITECTURE update job) — replay ordering is expressed as
  ordering in the window function instead of sequential upserts, which makes
  the whole build idempotent and restart-free.
- Every table then keeps exactly the rows whose (serial_no,
  file_name_source) match a winner: upsert of case_file and
  delete-and-rewrite of all child rows, in one join.

Output: builds/<build_id>/data/<table>/*.parquet, sorted by serial_no,
zstd, part files capped so row-group stats keep serial-range scans cheap
(schema §6).
"""

from __future__ import annotations

import json
from datetime import date, datetime, UTC
from pathlib import Path

import duckdb

from .config import PARSER_VERSION, Config
from .db import scalar
from .filenames import file_dt_source
from .schema import TABLES

PART_FILE_SIZE = "384MB"  # middle of the 256-512 MB target (schema §6)


class BuildError(RuntimeError):
    pass


def _part_path(cfg: Config, zip_name: str, table: str) -> Path:
    return cfg.silver_parts / Path(zip_name).stem / f"{table}.parquet"


def build_candidate(cfg: Config, cutoff: date, zips: list[Path]) -> Path:
    """Merge the parsed parts of `zips` into a new candidate build dir."""
    missing = [z.name for z in zips
               if not (cfg.silver_parts / z.stem / "_manifest.json").exists()]
    if missing:
        raise BuildError(
            f"{len(missing)} input zip(s) have no completed parse output "
            f"(e.g. {missing[0]}) — finish the parse stage first."
        )
    if not zips:
        raise BuildError("empty input set — nothing to build")

    build_id = datetime.now(UTC).strftime("build-%Y%m%d-%H%M%S")
    candidate = cfg.builds / build_id
    (candidate / "data").mkdir(parents=True)

    con = duckdb.connect()
    # Big-table sorts exceed RAM; make sure spill goes to the data volume
    # (defaults to the in-memory db's cwd otherwise) and drop the insertion-
    # order buffers — every COPY below has an explicit ORDER BY, which
    # preserve_insertion_order does not affect, so this only saves memory.
    spill = cfg.builds / "_duckdb_spill"
    spill.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{spill.as_posix()}'")
    con.execute("SET preserve_insertion_order = false")
    case_file_parts = [
        str(_part_path(cfg, z.name, "case_file")) for z in zips
    ]
    con.execute(
        """
        CREATE TEMP TABLE winners AS
        SELECT serial_no, file_name_source
        FROM (
            SELECT serial_no, file_name_source,
                   row_number() OVER (
                       PARTITION BY serial_no
                       ORDER BY file_dt_source DESC, file_name_source DESC
                   ) AS rn
            FROM read_parquet(?)
        )
        WHERE rn = 1
        """,
        [case_file_parts],
    )

    row_counts: dict[str, int] = {}
    for table in TABLES:
        parts = [str(p) for z in zips
                 if (p := _part_path(cfg, z.name, table)).exists()]
        out_dir = candidate / "data" / table
        out_dir.mkdir(parents=True)
        if not parts:  # a valid outcome for sparse satellite tables
            row_counts[table] = 0
            continue
        # NOTE: if a single daily file ever carried the same serial twice,
        # its child rows would duplicate here; case_file stays unique via
        # the QUALIFY below and reconcile profiles would surface the skew.
        # The row_number() has no ORDER BY, so *which* duplicate survives is
        # not deterministic — accepted deliberately: both rows come from the
        # same winning source file and are near-identical, so the choice does
        # not change downstream data. A true (serial, file, differing-content)
        # collision is a source anomaly the reconcile skew check would catch.
        dedupe = ("QUALIFY row_number() OVER (PARTITION BY t.serial_no) = 1"
                  if table == "case_file" else "")
        con.execute(
            f"""
            COPY (
                SELECT t.*
                FROM read_parquet(?) t
                JOIN winners w USING (serial_no, file_name_source)
                {dedupe}
                ORDER BY serial_no
            ) TO '{out_dir.as_posix()}'
            (FORMAT PARQUET, COMPRESSION ZSTD, FILE_SIZE_BYTES '{PART_FILE_SIZE}')
            """,
            [parts],
        )
        row_counts[table] = scalar(
            con,
            f"SELECT count(*) FROM read_parquet('{out_dir.as_posix()}/*.parquet')",
        )

    dailies = [dt for z in zips
               if (dt := file_dt_source(z.name)) and dt > cutoff]
    meta = {
        "build_id": build_id,
        "parser_version": PARSER_VERSION,
        "annual_cutoff": cutoff.isoformat(),
        "data_current_through": max(dailies, default=cutoff).isoformat(),
        "inputs": [z.name for z in zips],
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "row_counts": row_counts,
    }
    (candidate / "build_meta.json").write_text(json.dumps(meta, indent=2))
    con.close()
    return candidate


def read_meta(build_dir: Path) -> dict:
    return json.loads((build_dir / "build_meta.json").read_text())
