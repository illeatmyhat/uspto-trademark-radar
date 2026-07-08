"""duckdb fetch helpers.

`fetchone()` is typed `tuple | None` even for aggregate queries that always
produce exactly one row; these wrappers centralize the narrowing so call
sites stay clean and a genuinely empty result fails loudly.
"""

from __future__ import annotations

from typing import Any

import duckdb


def one_row(con: duckdb.DuckDBPyConnection, sql: str,
            params: list | None = None) -> tuple[Any, ...]:
    row = con.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError(f"query returned no rows: {' '.join(sql.split())[:120]}")
    return row


def scalar(con: duckdb.DuckDBPyConnection, sql: str,
           params: list | None = None) -> Any:
    return one_row(con, sql, params)[0]
