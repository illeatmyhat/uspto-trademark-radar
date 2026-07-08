"""DIY job ledger on SQLite (docs/adr/0003-diy-job-ledger.md).

One row per (work_unit, stage); unit of work is one zip per stage
("download X", "parse X"). States: pending -> claimed -> running -> done |
failed. Claiming is atomic (BEGIN IMMEDIATE + UPDATE ... RETURNING) and
leased: a claim expires unless heartbeated, so a dead worker's jobs become
claimable again. Recovery from any crash is simply re-running the stage —
everything not `done` gets re-claimed, and stage implementations are
idempotent.

The ledger also holds:
- `downloads`: lineage records; every silver row's `file_name_source` must
  trace back to one of these (reconcile gate, schema §5).
- `watermark`: key/value progress markers ("last_daily_integrated",
  "data_current_through", "last_annual_integrated").
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY,
    work_unit        TEXT NOT NULL,      -- e.g. zip filename
    stage            TEXT NOT NULL,      -- download | parse
    status           TEXT NOT NULL DEFAULT 'pending',
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    claimed_by       TEXT,
    lease_expires_at REAL,               -- unix epoch; NULL unless claimed/running
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (work_unit, stage)
);
CREATE INDEX IF NOT EXISTS jobs_stage_status ON jobs (stage, status);

CREATE TABLE IF NOT EXISTS downloads (
    file_name     TEXT PRIMARY KEY,
    product       TEXT NOT NULL,
    url           TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    etag          TEXT,               -- S3 ETag from USPTO; content MD5 when no "-N" suffix
    downloaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watermark (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

STATES = ("pending", "claimed", "running", "done", "failed")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: int
    work_unit: str
    stage: str
    status: str
    attempt_count: int


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self._migrate()
        self.db.commit()
        self.worker_id = f"{os.environ.get('COMPUTERNAME', 'host')}-pid{os.getpid()}"

    def _migrate(self) -> None:
        """Additive migrations for ledgers created by older code."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(downloads)")}
        for col in ("sha256", "etag"):
            if col not in cols:
                self.db.execute(f"ALTER TABLE downloads ADD COLUMN {col} TEXT")

    def close(self) -> None:
        self.db.close()

    # -- jobs ---------------------------------------------------------------

    def ensure_job(self, work_unit: str, stage: str) -> None:
        """Register a unit of work; no-op if it already exists (any state)."""
        now = _now_iso()
        self.db.execute(
            "INSERT INTO jobs (work_unit, stage, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (work_unit, stage) DO NOTHING",
            (work_unit, stage, now, now),
        )
        self.db.commit()

    def claim(self, stage: str, lease_seconds: int = 900) -> Job | None:
        """Atomically claim one job: pending, or claimed/running with an
        expired lease (dead worker). Returns None when nothing is claimable.
        """
        now = time.time()
        with self.db:  # implicit transaction; IMMEDIATE avoids upgrade races
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                """
                UPDATE jobs SET
                    status = 'running',
                    claimed_by = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE stage = ?
                      AND (status = 'pending'
                           OR (status IN ('claimed', 'running')
                               AND lease_expires_at < ?))
                    ORDER BY work_unit
                    LIMIT 1
                )
                RETURNING id, work_unit, stage, status, attempt_count
                """,
                (self.worker_id, now + lease_seconds, _now_iso(), stage, now),
            ).fetchone()
        return Job(*row) if row else None

    def heartbeat(self, job_id: int, lease_seconds: int = 900) -> None:
        self.db.execute(
            "UPDATE jobs SET lease_expires_at = ?, updated_at = ? WHERE id = ?",
            (time.time() + lease_seconds, _now_iso(), job_id),
        )
        self.db.commit()

    def complete(self, job_id: int) -> None:
        self._finish(job_id, "done", None)

    def fail(self, job_id: int, error: str) -> None:
        self._finish(job_id, "failed", error[:4000])

    def _finish(self, job_id: int, status: str, error: str | None) -> None:
        self.db.execute(
            "UPDATE jobs SET status = ?, error = ?, lease_expires_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (status, error, _now_iso(), job_id),
        )
        self.db.commit()

    def requeue(self, work_unit: str, stage: str) -> None:
        """Flip a done/failed job back to pending — used when its recorded
        output no longer exists (deleted cache, parser-version bump). Leaves
        running jobs alone: a live worker may not have written output yet."""
        self.db.execute(
            "UPDATE jobs SET status = 'pending', error = NULL, "
            "updated_at = ? WHERE work_unit = ? AND stage = ? "
            "AND status IN ('done', 'failed')",
            (_now_iso(), work_unit, stage),
        )
        self.db.commit()

    def retry_failed(self, stage: str | None = None) -> int:
        """Flip failed jobs back to pending (manual triage escape hatch)."""
        q = "UPDATE jobs SET status = 'pending', error = NULL, updated_at = ?"
        args: list = [_now_iso()]
        q += " WHERE status = 'failed'"
        if stage:
            q += " AND stage = ?"
            args.append(stage)
        cur = self.db.execute(q, args)
        self.db.commit()
        return cur.rowcount

    def status_counts(self) -> dict[tuple[str, str], int]:
        rows = self.db.execute(
            "SELECT stage, status, COUNT(*) FROM jobs GROUP BY stage, status"
        ).fetchall()
        return {(stage, status): n for stage, status, n in rows}

    def pending_or_failed(self, stage: str) -> list[tuple[str, str, str | None]]:
        return self.db.execute(
            "SELECT work_unit, status, error FROM jobs "
            "WHERE stage = ? AND status != 'done' ORDER BY work_unit",
            (stage,),
        ).fetchall()

    def is_done(self, work_unit: str, stage: str) -> bool:
        row = self.db.execute(
            "SELECT status FROM jobs WHERE work_unit = ? AND stage = ?",
            (work_unit, stage),
        ).fetchone()
        return bool(row) and row[0] == "done"

    # -- downloads (lineage) --------------------------------------------------

    def record_download(self, file_name: str, product: str, url: str,
                        size_bytes: int, sha256: str | None = None,
                        etag: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO downloads "
            "(file_name, product, url, size_bytes, sha256, etag, downloaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_name, product, url, size_bytes, sha256, etag, _now_iso()),
        )
        self.db.commit()

    def get_download(self, file_name: str) -> dict | None:
        row = self.db.execute(
            "SELECT file_name, product, url, size_bytes, sha256, etag, "
            "downloaded_at FROM downloads WHERE file_name = ?",
            (file_name,),
        ).fetchone()
        if row is None:
            return None
        keys = ("file_name", "product", "url", "size_bytes", "sha256",
                "etag", "downloaded_at")
        return dict(zip(keys, row, strict=True))

    def downloaded_files(self, product: str | None = None) -> set[str]:
        if product:
            rows = self.db.execute(
                "SELECT file_name FROM downloads WHERE product = ?", (product,)
            )
        else:
            rows = self.db.execute("SELECT file_name FROM downloads")
        return {r[0] for r in rows}

    # -- watermarks -----------------------------------------------------------

    def set_watermark(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO watermark (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key, value, _now_iso()),
        )
        self.db.commit()

    def get_watermark(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT value FROM watermark WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def watermarks(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT key, value FROM watermark"))
