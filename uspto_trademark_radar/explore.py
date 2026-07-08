"""Interactive exploration support: a DuckDB database of views over a build.

`trademark-radar explore` (re)creates data/silver.duckdb containing one view
per silver table pointing at the live build (or, pre-release, the newest
candidate). Any DuckDB client can then open it — python, the duckdb CLI,
harlequin, DBeaver — and query the tables by name without knowing the
on-disk layout. Views are zero-copy: queries scan the Parquet directly, so
"refreshing" after a new release is just re-running the command.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .lookups import LOOKUP_FILES, lookup_dir
from .schema import TABLES

STARTER_QUERIES = """\
-- Corpus size by decade
SELECT (year(filing_dt) / 10)::INT * 10 AS decade, count(*) AS filings
FROM case_file GROUP BY 1 ORDER BY 1;

-- Post-2015 filings in marketplace-gated classes, sliced by owner geography
SELECT cf.serial_no, cf.mark_id_char, cf.filing_dt, o.own_name
FROM case_file cf
JOIN owner o USING (serial_no)
JOIN intl_class ic USING (serial_no)
WHERE o.own_addr_country_cd = 'CN'
  AND cf.filing_dt >= DATE '2015-01-01'
  AND ic.intl_class_cd IN ('009','011','018','020','021','025','028')
LIMIT 100;

-- Highest-volume correspondents (filer clustering)
SELECT cor_addr_1, count(*) AS n
FROM correspondent GROUP BY 1 ORDER BY n DESC LIMIT 25;

-- TMA expungement/reexamination activity by event code (post-2021)
SELECT e.event_cd, any_value(l.description) AS description, count(*) AS n
FROM event e
JOIN lookup_event_codes l
  ON l.code = e.event_cd AND l.event_type_cd = e.event_type_cd
WHERE e.event_dt >= DATE '2021-12-18'
  AND (l.description ILIKE '%expungement%' OR l.description ILIKE '%reexam%')
GROUP BY 1 ORDER BY n DESC LIMIT 50;
"""


def make_views_db(build_dir: Path, db_path: Path) -> list[str]:
    """Create/refresh db_path with a view per table over build_dir.
    Returns the list of view names created.
    """
    con = duckdb.connect(str(db_path))
    created = []
    try:
        for table in TABLES:
            data_dir = build_dir / "data" / table
            if not any(data_dir.glob("*.parquet")):
                continue
            glob = data_dir.as_posix() + "/*.parquet"
            con.execute(
                f"CREATE OR REPLACE VIEW {table} AS "
                f"SELECT * FROM read_parquet('{glob}')"
            )
            created.append(table)
        # Code lookup tables ship with the package; all_varchar keeps
        # zero-padded codes like '000' from collapsing to integers.
        for name in LOOKUP_FILES:
            view = f"lookup_{name.removesuffix('.csv')}"
            path = (lookup_dir() / name).as_posix()
            con.execute(
                f"CREATE OR REPLACE VIEW {view} AS "
                f"SELECT * FROM read_csv('{path}', all_varchar=true)"
            )
            created.append(view)
        # No prepared params inside CREATE VIEW — inline the (trusted) path.
        meta = (build_dir / "build_meta.json").as_posix().replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW _build AS "
            f"SELECT * FROM read_json_auto('{meta}')"
        )
    finally:
        con.close()
    return created
