"""mark_features — per-filing feature columns derived from silver.

One row per application (keyed by serial_no, joins to silver case_file):
  mark_id_char         the wordmark text
  coined_mark_score    character n-gram improbability of the whole mark, [0,1]
  coined_token_max     max n-gram improbability over the mark's tokens, [0,1]
                       (robust to padding a coined token with an ordinary
                       word, e.g. 'ZORVEX GEAR')
  single_token         mark is one whitespace-delimited token
  is_standard_char     mark drawing code = standard characters
  is_marketplace_class filed in an Amazon-heavy class (9/11/18/20/21/25/28)
  filing_basis         'use'(1a) / 'intent'(1b) / 'foreign' / 'madrid' / null
  owner_country        applicant country code (public record; contains dirt)
  goods_item_count     number of ';'-separated goods/services items
  final_refusal_in     a final refusal issued (public prosecution-history fact)
  abandoned_no_use_in  abandoned for missing/defective use statement
  filing_year          year(filing_dt)

`final_refusal_in` / `abandoned_no_use_in` are the traces that mark-string
evasion tends to generate: swapping coined strings for real dictionary words
raises confusing-similarity refusals, and faking use raises use-statement
abandonments. Both are neutral, public prosecution-history facts.

Publishable (docs/adr/0008-publication-ethics.md); shipped under the same
US-government-work terms as the silver corpus.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import duckdb
from duckdb.func import FunctionNullHandling

from ..db import scalar
from .coined_scorer import FIT_SAMPLE, CoinedScorer, fit_scorer

VERSION = "1"
TABLE = f"mark_features_v{VERSION}"
MARKETPLACE_CLASSES = ("009", "011", "018", "020", "021", "025", "028")

# Prosecution-history event codes (see lookups/event_codes.csv).
FINAL_REFUSAL_EVENTS = ("CNCF", "CNFR", "GNCF", "GNCN", "GNFN", "GNFR")
USE_ABANDON_EVENTS = ("ABN6", "ABN7", "MAB6", "MAB7")


def build(silver_build_dir: Path, gold_root: Path,
          scorer: CoinedScorer | None = None, log=print) -> Path:
    con = duckdb.connect()
    con.execute("SET temp_directory = '" +
                (gold_root / "_spill").as_posix() + "'")

    def rp(tbl: str) -> str:
        return f"read_parquet('{(silver_build_dir / 'data' / tbl).as_posix()}/*.parquet')"

    if scorer is None:
        scorer = fit_scorer(
            con, (silver_build_dir / "data" / "case_file").as_posix() + "/*.parquet")
    con.create_function("coined", scorer.score, ["VARCHAR"], "DOUBLE",
                        null_handling=FunctionNullHandling.SPECIAL)
    con.create_function("coined_tok", scorer.score_token_max, ["VARCHAR"],
                        "DOUBLE", null_handling=FunctionNullHandling.SPECIAL)
    calib = ("uncalibrated" if scorer.lo is None
             else f"calib lo={scorer.lo:.3f} hi={scorer.hi:.3f}")
    log(f"[gold] coined scorer fit ({calib})")

    out_dir = gold_root / TABLE
    out_dir.mkdir(parents=True, exist_ok=True)
    # COPY TO a directory refuses any non-empty target; a rebuild replaces
    # the table, so clear stale parts and lineage.
    for stale in out_dir.iterdir():
        stale.unlink()
    con.execute(f"""
      COPY (
        SELECT
          cf.serial_no,
          cf.mark_id_char,
          coined(cf.mark_id_char) AS coined_mark_score,
          coined_tok(cf.mark_id_char) AS coined_token_max,
          (cf.mark_id_char IS NOT NULL
             AND strpos(trim(cf.mark_id_char),' ')=0) AS single_token,
          (cf.mark_draw_cd='4') AS is_standard_char,
          EXISTS(SELECT 1 FROM {rp('intl_class')} ic WHERE ic.serial_no=cf.serial_no
             AND ic.intl_class_cd IN {MARKETPLACE_CLASSES}) AS is_marketplace_class,
          CASE WHEN cf.use_af_in THEN 'use'
               WHEN cf.use_intent_in THEN 'intent'
               WHEN cf.for_app_in OR cf.for_reg_in THEN 'foreign'
               WHEN cf.intl_reg_in THEN 'madrid' END AS filing_basis,
          (SELECT o.own_addr_country_cd FROM {rp('owner')} o
             WHERE o.serial_no=cf.serial_no
             -- original applicant first; total order (own_seq alone is unique
             -- only within a party type) keeps the pick deterministic.
             ORDER BY (o.own_type_cd='10') DESC, o.own_seq, o.own_name
             LIMIT 1) AS owner_country,
          (SELECT max(length(s.statement_text)
                      - length(replace(s.statement_text,';','')) + 1)
             FROM {rp('statement')} s WHERE s.serial_no=cf.serial_no
             AND s.statement_type_cd LIKE 'GS%') AS goods_item_count,
          EXISTS(SELECT 1 FROM {rp('event')} e WHERE e.serial_no=cf.serial_no
             AND e.event_cd IN {FINAL_REFUSAL_EVENTS}) AS final_refusal_in,
          EXISTS(SELECT 1 FROM {rp('event')} e WHERE e.serial_no=cf.serial_no
             AND e.event_cd IN {USE_ABANDON_EVENTS}) AS abandoned_no_use_in,
          year(cf.filing_dt) AS filing_year
        FROM {rp('case_file')} cf
        WHERE cf.mark_id_char IS NOT NULL
        ORDER BY cf.serial_no
      ) TO '{out_dir.as_posix()}'
      (FORMAT PARQUET, COMPRESSION ZSTD, FILE_SIZE_BYTES '384MB')
    """)

    n = scalar(
        con,
        f"SELECT count(*) FROM read_parquet('{out_dir.as_posix()}/*.parquet')",
    )
    meta = {
        "table": TABLE, "version": VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "scorer": {"order": scorer.order, "lo": scorer.lo, "hi": scorer.hi,
                   "fit_sample": FIT_SAMPLE, "fit_basis": "wordmarks filed <2015"},
        "n_marks": n,
        "scope": "publishable per-filing features, US government work (ADR 0008).",
    }
    (out_dir / "_lineage.json").write_text(json.dumps(meta, indent=2))
    con.close()
    log(f"[gold] {TABLE}: {n:,} marks -> {out_dir}")
    return out_dir
