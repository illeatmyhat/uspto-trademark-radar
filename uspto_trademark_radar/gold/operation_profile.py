"""operation_profile - per-operation portfolio statistics.

Groups post-2015 applications into filing operations and computes descriptive
statistics for each: filing count, owner concentration (distinct owners per
filing), mark-composition rates (coined share, design share),
class distribution (marketplace-gated share), filing-basis mix, goods-list
length, a burst measure, and prosecution-outcome rates (final refusals,
use-statement abandonments). A composite `peculiarity_score` summarizes how
far a portfolio's composition sits from the corpus norm; `is_shared_service`
annotates high-name-diversity, low-concentration addresses.

Grouping uses `entity_resolution.operation_keys`: each filing is keyed on its
most reliable non-hub signal (canonical attorney name first, address second),
which resists the cheap address-rotation evasion without transitively merging
unrelated filers into a blob (see that module's docstring for the tradeoff).
Coinedness is scored per token (`coined_token_max`), so padding a coined token
with an ordinary word does not hide it.

Local-only table (docs/adr/0008-publication-ethics.md): written under
data/gold/, never published. Grouping leans on the attorney name, which is
why this table stays local; the corpus carries no bar number or other
attorney credential, so none is used.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import duckdb
import pyarrow as pa
from duckdb.func import FunctionNullHandling

from ..db import scalar
from . import entity_resolution
from .coined_scorer import CoinedScorer, fit_scorer
from .mark_features import FINAL_REFUSAL_EVENTS, USE_ABANDON_EVENTS

VERSION = "1"
TABLE = f"operation_profile_v{VERSION}"
# Local-only serial_no -> op_key mapping, persisted so the detector's Tier 3
# can map a qualifying operation back to its filings without re-running the
# grouping. Contains the op_key (which embeds the attorney name), so it stays
# local and is never published (ADR 0008); only the per-filing membership
# *fact* (a mark is in a large cluster) leaves, via detector_lists (ADR 0017).
MEMBERSHIP_TABLE = f"operation_membership_v{VERSION}"

# Marketplace-gated intl classes (Amazon Brand Registry-adjacent).
MARKETPLACE_CLASSES = ("009", "011", "018", "020", "021", "025", "028")

# A mark counts as coined when its most improbable token scores at or above
# this (same scale as mark_features.coined_token_max: 1.0 = most improbable).
COINED_THRESHOLD = 0.85

# Component weights for the composite score (rates in [0,1]; weighted mean;
# sum to 1.0). A transparent weighting, not a trained model - adjust and
# re-version openly. Prosecution-outcome rates are reported, not weighted:
# they are validation signals (does evasion leave traces?), not inputs.
_WEIGHTS = {
    "owner_turnover": 0.25,    # distinct owners per filing
    "coined_rate": 0.25,       # coined marks (per-token)
    "marketplace_rate": 0.20,  # marketplace-gated classes
    "use1a_rate": 0.15,        # section 1(a) use basis
    "goods_chaos": 0.15,       # goods-list length (item count)
}
_MIN_MARKS = 20                # below this an operation is too small to profile


def weighted_score_expr(weights: dict[str, float] = _WEIGHTS) -> str:
    """SQL expression for the composite peculiarity score: the weighted sum
    of the component rate columns, each defaulted to 0 when missing. Shared
    so the sensitivity harness (resolution_audit) reproduces this exact
    formula rather than a drifting copy."""
    return " + ".join(f"{v}*coalesce({k},0)" for k, v in weights.items())


def _filings_sql(build_dir: Path) -> str:
    def rp(tbl: str) -> str:
        return f"read_parquet('{(build_dir / 'data' / tbl).as_posix()}/*.parquet')"
    return f"""
    SELECT cf.serial_no,
      cf.attorney_name AS raw_name,
      canon_fn(cf.attorney_name) AS canon_name,
      addr_key_fn(c.cor_addr_2, c.cor_addr_3, c.cor_addr_4) AS addr_key,
      cf.filing_dt,
      cf.use_af_in::INT AS use1a,
      (cf.mark_id_char IS NOT NULL
         AND coalesce(coined_tok(cf.mark_id_char), 0) >= {COINED_THRESHOLD})::INT AS coined,
      (cf.mark_draw_cd IN ('2','3'))::INT AS design,
      (SELECT o.own_addr_country_cd FROM {rp('owner')} o
         WHERE o.serial_no=cf.serial_no
         ORDER BY (o.own_type_cd='10') DESC, o.own_seq, o.own_name LIMIT 1) AS cc,
      (SELECT upper(trim(o.own_name)) FROM {rp('owner')} o
         WHERE o.serial_no=cf.serial_no
         ORDER BY (o.own_type_cd='10') DESC, o.own_seq, o.own_name LIMIT 1) AS applicant,
      EXISTS(SELECT 1 FROM {rp('intl_class')} ic WHERE ic.serial_no=cf.serial_no
         AND ic.intl_class_cd IN {MARKETPLACE_CLASSES})::INT AS mkt,
      (SELECT max(length(s.statement_text)
                  - length(replace(s.statement_text,';','')) + 1)
         FROM {rp('statement')} s WHERE s.serial_no=cf.serial_no
         AND s.statement_type_cd LIKE 'GS%') AS goods_items,
      EXISTS(SELECT 1 FROM {rp('event')} e WHERE e.serial_no=cf.serial_no
         AND e.event_cd IN {FINAL_REFUSAL_EVENTS})::INT AS refused,
      EXISTS(SELECT 1 FROM {rp('event')} e WHERE e.serial_no=cf.serial_no
         AND e.event_cd IN {USE_ABANDON_EVENTS})::INT AS no_use_abandon
    FROM {rp('case_file')} cf
    LEFT JOIN {rp('correspondent')} c USING (serial_no)
    WHERE cf.filing_dt >= DATE '2015-01-01'
      AND (cf.attorney_name IS NOT NULL OR c.cor_addr_2 IS NOT NULL)
    """


_AGG_SQL = f"""
WITH owners AS (SELECT op_key, applicant, count(*) c FROM mkc
                WHERE applicant IS NOT NULL GROUP BY 1,2),
days AS (SELECT op_key, filing_dt, count(*) c FROM mkc GROUP BY 1,2)
SELECT
  m.op_key,
  count(*) AS n_marks,
  count(DISTINCT m.raw_name) AS n_name_spellings,
  count(DISTINCT m.canon_name) AS n_canonical_names,
  count(DISTINCT m.addr_key) AS n_addresses,
  (SELECT count(*) FROM owners o WHERE o.op_key=m.op_key)*1.0/count(*) AS owner_turnover,
  avg((m.cc='CN')::INT) AS pct_cn,
  avg(m.coined) AS coined_rate,
  avg(m.mkt) AS marketplace_rate,
  avg(m.use1a) AS use1a_rate,
  avg(m.design) AS design_rate,
  avg(m.goods_items) AS avg_goods_items,
  least(avg(m.goods_items)/20.0, 1.0) AS goods_chaos,
  avg(m.refused) AS refusal_rate,
  avg(m.no_use_abandon) AS no_use_abandon_rate,
  (SELECT max(c) FROM days d WHERE d.op_key=m.op_key) AS max_marks_one_day,
  min(year(m.filing_dt)) AS first_year,
  max(year(m.filing_dt)) AS last_year
FROM mkc m
GROUP BY m.op_key
HAVING count(*) >= {_MIN_MARKS}
"""


def build(silver_build_dir: Path, gold_root: Path,
          scorer: CoinedScorer | None = None, log=print) -> Path:
    """Build operation_profile_v{VERSION} from a silver build directory.
    Writes a single parquet under gold_root/<table>/ and a lineage json.
    Returns the table directory. Local-only (see module docstring).
    """
    con = duckdb.connect()
    entity_resolution.register(con)
    if scorer is None:
        scorer = fit_scorer(
            con, (silver_build_dir / "data" / "case_file").as_posix() + "/*.parquet")
    con.create_function("coined_tok", scorer.score_token_max, ["VARCHAR"],
                        "DOUBLE", null_handling=FunctionNullHandling.SPECIAL)

    out_dir = gold_root / TABLE
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.iterdir():
        stale.unlink()

    con.execute(f"CREATE TEMP TABLE mk AS {_filings_sql(silver_build_dir)}")
    rows = con.execute("SELECT serial_no, addr_key, canon_name FROM mk").fetchall()
    comp = entity_resolution.operation_keys(rows)
    log(f"[gold] {len(comp):,} attributable filings -> "
        f"{len(set(comp.values())):,} operations (pre-threshold)")
    comp_tbl = pa.table({"serial_no": pa.array(comp.keys(), pa.string()),
                         "op_key": pa.array(comp.values(), pa.string())})
    con.register("comp", comp_tbl)

    # Persist the local serial_no -> op_key membership (see MEMBERSHIP_TABLE).
    mem_dir = gold_root / MEMBERSHIP_TABLE
    mem_dir.mkdir(parents=True, exist_ok=True)
    for stale in mem_dir.iterdir():
        stale.unlink()
    con.execute(f"COPY comp TO '{mem_dir.as_posix()}/part-000.parquet' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)")

    con.execute("CREATE TEMP TABLE mkc AS "
                "SELECT m.*, comp.op_key FROM mk m JOIN comp USING (serial_no)")

    w = weighted_score_expr()
    con.execute(f"CREATE TEMP TABLE op AS SELECT * FROM ({_AGG_SQL})")
    con.execute(f"""
      COPY (
        SELECT *,
          round({w}, 4) AS peculiarity_score,
          -- high name-diversity, low-concentration operations (multi-tenant
          -- filing services / large firms): many distinct filers, low coined
          -- share, short goods lists.
          (n_canonical_names >= 20 AND coined_rate < 0.20
             AND pct_cn < 0.30 AND avg_goods_items < 6)::BOOLEAN AS is_shared_service
        FROM op
        ORDER BY peculiarity_score DESC, op_key
      ) TO '{out_dir.as_posix()}/part-000.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    n_ops = scalar(
        con,
        f"SELECT count(*) FROM read_parquet('{out_dir.as_posix()}/*.parquet')",
    )
    meta = {
        "table": TABLE,
        "version": VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "grouping": "non-transitive; canonical name first then address, "
                    f"names spanning > {entity_resolution.NAME_HUB_MAX_ADDRESSES} "
                    f"addresses and addresses with > "
                    f"{entity_resolution.ADDR_HUB_MAX_NAMES} names excluded as hubs",
        "weights": _WEIGHTS,
        "min_marks": _MIN_MARKS,
        "scorer": {"order": scorer.order, "lo": scorer.lo, "hi": scorer.hi,
                   "coined_threshold": COINED_THRESHOLD, "per_token": True},
        "n_operations": n_ops,
        "scope": "local-only; not part of the publish path (ADR 0008).",
    }
    (out_dir / "_lineage.json").write_text(json.dumps(meta, indent=2))
    con.close()
    log(f"[gold] {TABLE}: {n_ops:,} operations -> {out_dir}")
    return out_dir
