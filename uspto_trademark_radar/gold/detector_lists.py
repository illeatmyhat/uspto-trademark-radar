"""detector_lists - the shippable lists a downstream detector fetches.

Two plain newline-delimited files of normalized mark keys, built to be
consumed exactly like an ad-blocker filter list: fetch, cache, membership-test.
No per-row columns - the consumer already has the mark text on the page and
builds its own USPTO search link from it (see CONSUMERS.md).

  tier1.txt  adjudicated blocklist - marks on a filing the USPTO itself
             sanctioned (order-for-sanctions events or the sanctions-
             terminated status). A public-record fact, not our inference.
  tier2.txt  screened candidates - marks whose per-filing features clear a
             recommended default threshold. A hint to inspect, not a verdict.

Optionally also writes origin_<cc>.txt - a separate, opt-in origin filter
(e.g. origin_cn.txt, "Origin - China") keyed on the registered owner country
of record. It is NOT part of the tier ladder and NOT a junk signal; see
ORIGIN_COUNTRIES below.

Both are keyed on a normalized mark string so a consumer can match the brand
text it reads off a page:

    key = drop every character that is not a-z, A-Z, or 0-9, then uppercase
    "Zorvex Gear!" -> "ZORVEXGEAR"

Stripping non-alphanumerics BEFORE uppercasing is deliberate: it keeps the
DuckDB key identical to a JS consumer's
`s.replace(/[^A-Za-z0-9]/g,'').toUpperCase()`. Uppercasing first would let
SQL upper() and JS toUpperCase() disagree on non-ASCII letters (e.g. JS folds
'ss' -> 'SS' via toUpperCase on some inputs) and silently mute a blocklist
entry; once the non-ASCII is gone, only ASCII A-Z remain and the two agree.

The sanctions codes are imported from `evaluation` so the blocklist and the
outcome labels can never drift apart. Core dependencies only (duckdb) - safe
to run in the monthly pipeline, which does not install the analysis group.
Publishable per ADR 0008: mark strings are public record and the lists key on
the filing, never on a person.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import duckdb

from .evaluation import SANCTION_EVENTS, SANCTION_STATUS
from .mark_features import TABLE as FEATURES_TABLE
from .operation_profile import TABLE as PROFILE_TABLE

VERSION = "1"
KEY_RULE = "drop every character that is not a-z, A-Z, or 0-9, then uppercase"
KEY_SQL = "upper(regexp_replace({col},'[^A-Za-z0-9]','','g'))"

# Tier 2 screening score: a fixed, transparent blend of the published
# per-filing feature columns (no fitting, no standardization - see ADR 0016 for
# the fitted instrument). Range [0, 2.0]. A mark scoring at or above
# TIER2_THRESHOLD is emitted as a candidate to inspect. The default is
# deliberately conservative - a high-signal set where the mark-string, class,
# goods, and single-token signals largely agree - which keeps the shipped list
# small (~tens of thousands of marks, on par with tier 1) and false alarms low.
# Lower it for recall; the build reports the resulting count so it can be tuned.
TIER2_SCORE_SQL = (
    "coined_token_max"
    " + 0.5 * is_marketplace_class::INT"
    " + 0.3 * (coalesce(goods_item_count, 0) > 20)::INT"
    " + 0.2 * single_token::INT"
)
TIER2_THRESHOLD = 1.8

# Tier 3 (ADR 0017): marks belonging to a large, high-churn filing operation.
# A structural fact keyed on the filing, NOT an accusation and NOT a name -
# the operation grouping is local (operation_profile), only the membership
# fact ships. Gate chosen empirically (churn sweep): size >= 1000 puts the
# grouping in its ~1.0-precision band, churn >= 0.6 excludes near-zero-churn
# corporate portfolios while keeping genuine churn operations, and the pair
# separates USPTO-adjudicated sanctions at ~6x the base rate. Owner-country is
# deliberately NOT in the gate: the phenomenon is CN-concentrated, but the
# instrument screens filing behavior, not nationality.
TIER3_MIN_SIZE = 1000
TIER3_MIN_CHURN = 0.6

# Origin filter - NOT part of the tier ladder and NOT a junk signal. A separate,
# opt-in, off-by-default list of distinct mark keys whose filing lists a given
# registered owner country. Owner-country is deliberately excluded from the junk
# gates (ADR 0017, tier 3): a sophisticated operation just registers through a
# shell in another country to dodge a nationality gate, so it is evadable there.
# It ships here only as a clearly-labeled origin *preference* (like a
# country-of-origin / "Made in ..." filter). It is filing origin - the owner
# country *of record* - not manufacture: a CN-owned brand that files through a
# US entity (Anker, DJI) reads as US, while a CN-filed brand (UGREEN) reads CN.
ORIGIN_COUNTRIES = {"CN": "China"}


def _write_list(con: duckdb.DuckDBPyConnection, query: str, out: Path) -> int:
    """COPY a single-column query to a header-less file (one value per line)
    and return the row count COPY reports - no second pass over the query,
    which for tier 2 is a full scan of the whole mark_features table. Keys are
    A-Z0-9 only, so no CSV escaping."""
    row = con.execute(
        f"COPY ({query}) TO '{out.as_posix()}' (FORMAT CSV, HEADER false)"
    ).fetchone()
    return row[0] if row else 0


def build(silver_build_dir: Path, features_dir: Path, gold_root: Path,
          tier2_threshold: float = TIER2_THRESHOLD,
          profile_dir: Path | None = None, membership_dir: Path | None = None,
          tier3_min_size: int = TIER3_MIN_SIZE,
          tier3_min_churn: float = TIER3_MIN_CHURN,
          origin_countries: dict[str, str] | None = None, log=print) -> Path:
    """Write tier1.txt, tier2.txt, and _manifest.json under
    gold_root/detector_v<VERSION>/. `features_dir` is the built mark_features
    table (trademark-radar gold); `silver_build_dir` supplies the adjudicated
    outcomes. If `profile_dir` and `membership_dir` (the local operation
    grouping) are given, also write tier3.txt (ADR 0017)."""
    # tier 1 is derived from silver_build_dir but tier 2 from the pre-built
    # features table; refuse to mix two corpora (which would silently ship a
    # tier 2 from a stale build and mislabel its provenance in the manifest).
    feat_meta = json.loads((features_dir / "_lineage.json").read_text())
    if feat_meta.get("silver_build") != silver_build_dir.name:
        raise SystemExit(
            f"mark_features was built from silver '{feat_meta.get('silver_build')}'"
            f" but the live build is '{silver_build_dir.name}' - re-run "
            "`trademark-radar gold` so tier1 and tier2 come from one corpus")
    con = duckdb.connect()

    def rp(tbl: str) -> str:
        return (f"read_parquet('"
                f"{(silver_build_dir / 'data' / tbl).as_posix()}/*.parquet')")

    feat = f"read_parquet('{features_dir.as_posix()}/*.parquet')"
    sanction_ev = ", ".join(f"'{c}'" for c in SANCTION_EVENTS)
    cf_key = KEY_SQL.format(col="cf.mark_id_char")
    ft_key = KEY_SQL.format(col="mark_id_char")

    out_dir = gold_root / f"detector_v{VERSION}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.iterdir():
        stale.unlink()

    # Tier 1: distinct mark keys on any adjudicated (sanctioned) filing.
    tier1_q = f"""
      WITH flagged AS (
        SELECT DISTINCT serial_no FROM {rp('event')}
          WHERE event_cd IN ({sanction_ev})
        UNION
        SELECT serial_no FROM {rp('case_file')}
          WHERE status_cd = '{SANCTION_STATUS}')
      SELECT DISTINCT {cf_key} AS mark_key
      FROM flagged f JOIN {rp('case_file')} cf USING (serial_no)
      WHERE cf.mark_id_char IS NOT NULL AND {cf_key} <> ''
      ORDER BY mark_key
    """
    n1 = _write_list(con, tier1_q, out_dir / "tier1.txt")

    # Tier 2: distinct mark keys whose best-scoring filing clears the
    # threshold. Grouped so the list is unique per mark, like tier 1.
    tier2_q = f"""
      SELECT mark_key FROM (
        SELECT {ft_key} AS mark_key, max({TIER2_SCORE_SQL}) AS score
        FROM {feat}
        WHERE mark_id_char IS NOT NULL AND {ft_key} <> ''
        GROUP BY 1)
      WHERE score >= {tier2_threshold}
      ORDER BY mark_key
    """
    n2 = _write_list(con, tier2_q, out_dir / "tier2.txt")

    # Tier 3: distinct mark keys on filings that belong to a large, high-churn,
    # non-shared-service operation. The op_key (which embeds the attorney name)
    # never leaves the join - only the resulting mark keys are written.
    tier3 = None
    if profile_dir is not None and membership_dir is not None:
        prof = f"read_parquet('{profile_dir.as_posix()}/*.parquet')"
        mem = f"read_parquet('{membership_dir.as_posix()}/*.parquet')"
        tier3_q = f"""
          SELECT DISTINCT {cf_key} AS mark_key
          FROM {mem} m
          JOIN {prof} p ON p.op_key = m.op_key
          JOIN {rp('case_file')} cf ON cf.serial_no = m.serial_no
          WHERE p.n_marks >= {tier3_min_size}
            AND p.owner_turnover >= {tier3_min_churn}
            AND NOT p.is_shared_service
            AND cf.mark_id_char IS NOT NULL AND {cf_key} <> ''
          ORDER BY mark_key
        """
        n3 = _write_list(con, tier3_q, out_dir / "tier3.txt")
        tier3 = {"file": "tier3.txt", "n_marks": n3,
                 "profile_table": PROFILE_TABLE,
                 "gate": f"operation n_marks >= {tier3_min_size} and "
                         f"owner_turnover >= {tier3_min_churn} and "
                         "not is_shared_service",
                 "note": "structural membership fact; not an accusation; "
                         "owner-country not in the gate (ADR 0017)."}

    # Origin filter(s): distinct mark keys per registered owner country. Written
    # as origin_<cc>.txt, kept out of the tier ladder and grouped under a
    # separate manifest section so a consumer never mistakes an origin
    # preference for a junk verdict.
    countries = ORIGIN_COUNTRIES if origin_countries is None else origin_countries
    origins = {}
    for cc, label in countries.items():
        origin_q = f"""
          SELECT DISTINCT {ft_key} AS mark_key
          FROM {feat}
          WHERE owner_country = '{cc}'
            AND mark_id_char IS NOT NULL AND {ft_key} <> ''
          ORDER BY mark_key
        """
        n = _write_list(con, origin_q, out_dir / f"origin_{cc.lower()}.txt")
        origins[cc] = {"file": f"origin_{cc.lower()}.txt", "n_marks": n,
                       "label": f"Origin - {label}"}

    manifest = {
        "version": VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "features_table": FEATURES_TABLE,
        "key_rule": KEY_RULE,
        "tier1": {"file": "tier1.txt", "n_marks": n1,
                  "source": "USPTO-adjudicated sanctions "
                            f"(events {list(SANCTION_EVENTS)}, status "
                            f"{SANCTION_STATUS})"},
        "tier2": {"file": "tier2.txt", "n_marks": n2,
                  "threshold": tier2_threshold,
                  "score": "coined_token_max + 0.5*marketplace_class"
                           " + 0.3*(goods_item_count>20) + 0.2*single_token"},
        "scope": "public-record mark strings, keyed on the filing (ADR 0008).",
    }
    if tier3 is not None:
        manifest["tier3"] = tier3
    if origins:
        manifest["origins"] = {
            "note": "Opt-in origin filter, off by default in consumers; NOT a "
                    "junk signal and NOT part of the tier ladder. Keyed on the "
                    "registered owner country of record (filing origin, not "
                    "manufacture). Owner-country is excluded from the junk gates "
                    "(ADR 0017); it ships here only as a separate, labeled list.",
            "lists": origins,
        }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    con.close()
    t3 = f", tier3={tier3['n_marks']:,} marks" if tier3 is not None else ""
    org = "".join(f", {v['label']}={v['n_marks']:,} marks"
                  for v in origins.values())
    log(f"[detector] tier1={n1:,} marks, tier2={n2:,} marks "
        f"(threshold {tier2_threshold}){t3}{org} -> {out_dir}")
    return out_dir
