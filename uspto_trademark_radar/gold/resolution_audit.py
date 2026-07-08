"""resolution_audit - measuring the operation grouping (ADR 0011, ADR 0014).

Two validation artifacts for the non-transitive keying in
`entity_resolution.operation_keys`:

1. **Precision audit sample** (`build_audit_sample`): a deterministic,
   size-stratified sample of profiled operations with each group's raw
   evidence - attorney-name spellings, correspondent addresses, applicant
   names and countries - laid out for a manual coherence review. The audit
   question per operation: could these filings plausibly share one filing
   operation? Over-merge (members clearly from unrelated filers) is the
   failure being measured; fragmentation of one filer across several
   operations is the accepted safe direction (ADR 0011) and does not count
   against precision. The sample also renders a self-contained local
   review app (`review.html`, evidence inlined) whose exported verdicts
   are name-free and committable; `score_verdicts` turns them into a
   precision estimate with Wilson intervals. The evidence and the app
   embed attorney names and stay local; only the verdicts leave the box.

2. **Keying sensitivity analysis** (`sensitivity`): regroups the corpus
   under alternative keying rules - stricter/looser hub caps, address-first
   priority, single-signal (name-only / address-only) - and re-derives the
   operation-profile statistics under each, so headline results can be
   shown to survive the grouping choice rather than depend on it. Coined
   scores come from the persisted mark_features table; no scorer refit.

3. **Co-location diagnostic** (`colocation`): ranks non-hub correspondent
   addresses that host two or more distinct non-hub attorney names with
   peculiar-looking portfolios, as an inspection list. It reads the corpus
   only - it merges no operations and asserts nothing (co-location is a
   weak, high-false-positive flag; shared offices are ordinary). It exists
   to point a human at candidates, never to draw a conclusion.

Both artifacts are LOCAL-ONLY (ADR 0008): the audit evidence contains
attorney names and is never published. Everything is deterministic given
the build (ADR 0013): sampling orders by a content hash, not an RNG, and
grouping depends only on the row set.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa

from ..db import one_row, scalar
from . import entity_resolution, mark_features, operation_profile
from .operation_profile import (
    _MIN_MARKS,
    _WEIGHTS,
    COINED_THRESHOLD,
    MARKETPLACE_CLASSES,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

VERSION = "1"
AUDIT_TABLE = f"resolution_audit_v{VERSION}"
SENSITIVITY_FILE = "resolution_sensitivity.json"
REVIEW_APP = "review.html"
# Verdicts the review app emits and score_verdicts recognizes; only the
# first two enter the precision denominator.
VERDICTS = ("coherent", "mixed", "unclear")

COLOCATION_FILE = "resolution_colocation.json"
# A name filing fewer than this at one address is too thin to characterize.
CO_MIN_FILINGS_PER_NAME = 5
# Cap the inspection list; the full qualifying count is always logged.
CO_REPORT_CAP = 200
# Distinct co-located names to list as evidence per reported address.
CO_NAMES_PER_ADDR = 15

Row = tuple[str, str | None, str | None]  # (serial_no, address_key, name)

# Keying variants: mode plus the hub caps (name-spanning-addresses,
# address-serving-names). "shipped" is exactly ADR 0011 as built;
# the others answer "would the results change under a different rule?".
VARIANTS: dict[str, tuple[str, int, int]] = {
    "shipped": ("name_first", entity_resolution.NAME_HUB_MAX_ADDRESSES,
                entity_resolution.ADDR_HUB_MAX_NAMES),
    "strict_hubs": ("name_first", 25, 12),
    "loose_hubs": ("name_first", 100, 50),
    "addr_first": ("addr_first", entity_resolution.NAME_HUB_MAX_ADDRESSES,
                   entity_resolution.ADDR_HUB_MAX_NAMES),
    "name_only": ("name_only", 0, 0),
    "addr_only": ("addr_only", 0, 0),
}

# Audit strata over operation size (lo inclusive, hi exclusive, quota),
# plus the largest operations overall, always included: an over-merge
# there does the most damage.
SAMPLE_STRATA: tuple[tuple[int, int | None, int], ...] = (
    (_MIN_MARKS, 100, 45),
    (100, 1000, 35),
    (1000, None, 20),
)
FORCED_LARGEST = 5

REVIEW_GUIDE = (
    "Open review.html in a browser. For each operation, review the "
    "attorney-name spellings, correspondent addresses, and applicant names "
    "together and record a verdict: 'coherent' (the members plausibly share "
    "one filing operation), 'mixed' (members clearly belong to unrelated "
    "filers - an over-merge), or 'unclear'. Precision = coherent / (coherent "
    "+ mixed). One filer split across several operations is NOT an error "
    "here - ADR 0011 accepts fragmentation as the safe failure; only "
    "over-merge counts against precision. Export the name-free verdicts.json "
    "and score it with `trademark-radar resolution-score`."
)


def variant_keys(rows: Sequence[Row], variant: str) -> dict[str, str]:
    """Operation assignment for one keying variant; rows are
    (serial_no, address_key, canonical_name) as in
    `entity_resolution.operation_keys`. "shipped" delegates to that
    function; single-signal variants ignore the other signal entirely."""
    mode, name_cap, addr_cap = VARIANTS[variant]
    if mode == "name_first":
        return entity_resolution.operation_keys(list(rows), name_cap, addr_cap)
    if mode == "name_only":
        return {s: f"N:{name}" for s, _, name in rows if name}
    if mode == "addr_only":
        return {s: f"A:{addr}" for s, addr, _ in rows if addr}
    # addr_first - the mirror image of operation_keys: a non-hub address
    # is preferred, the non-hub name is the fallback.
    name_addrs: dict[str, set[str]] = {}
    addr_names: dict[str, set[str]] = {}
    for _, addr, name in rows:
        if addr and name:
            name_addrs.setdefault(name, set()).add(addr)
            addr_names.setdefault(addr, set()).add(name)
    out: dict[str, str] = {}
    for serial, addr, name in rows:
        if addr and len(addr_names.get(addr, ())) <= addr_cap:
            out[serial] = f"A:{addr}"
        elif name and len(name_addrs.get(name, ())) <= name_cap:
            out[serial] = f"N:{name}"
        elif addr:
            out[serial] = f"A:{addr}"
        elif name:
            out[serial] = f"N:{name}"
    return out


def _rp(build_dir: Path, tbl: str) -> str:
    return f"read_parquet('{(build_dir / 'data' / tbl).as_posix()}/*.parquet')"


def load_base(con: duckdb.DuckDBPyConnection, silver_build_dir: Path,
              gold_root: Path) -> None:
    """Materialize temp table `base`: one row per post-2015 attributable
    filing, carrying the grouping signals (canonical name, address key)
    plus the operation-profile inputs. Coinedness joins in from the
    persisted mark_features table so the scorer is not refit.

    Fails loudly if mark_features was built from a different silver build
    than the one being validated: the coined scores would then be joined
    to the wrong filings and the shipped variant would silently stop
    reproducing the real operation_profile."""
    mf_dir = gold_root / mark_features.TABLE
    lineage = mf_dir / "_lineage.json"
    if lineage.exists():
        mf_build = json.loads(lineage.read_text()).get("silver_build")
        if mf_build and mf_build != silver_build_dir.name:
            raise SystemExit(
                f"mark_features was built from {mf_build!r} but validation "
                f"targets {silver_build_dir.name!r}; rebuild the gold tables "
                "on the live build first (trademark-radar gold)")
    entity_resolution.register(con)
    mf_glob = mf_dir.as_posix() + "/*.parquet"
    con.execute(f"""
    CREATE TEMP TABLE base AS
    WITH owner_pick AS (
      SELECT serial_no, cc, applicant FROM (
        SELECT serial_no, own_addr_country_cd AS cc,
               upper(trim(own_name)) AS applicant,
               row_number() OVER (PARTITION BY serial_no
                 ORDER BY (own_type_cd='10') DESC, own_seq, own_name) AS rn
        FROM {_rp(silver_build_dir, 'owner')})
      WHERE rn = 1),
    goods AS (
      SELECT serial_no,
             max(length(statement_text)
                 - length(replace(statement_text, ';', '')) + 1) AS goods_items
      FROM {_rp(silver_build_dir, 'statement')}
      WHERE statement_type_cd LIKE 'GS%' GROUP BY serial_no),
    marketplace AS (
      SELECT DISTINCT serial_no FROM {_rp(silver_build_dir, 'intl_class')}
      WHERE intl_class_cd IN {MARKETPLACE_CLASSES})
    SELECT cf.serial_no,
      cf.attorney_name AS raw_name,
      canon_fn(cf.attorney_name) AS canon_name,
      addr_key_fn(c.cor_addr_2, c.cor_addr_3, c.cor_addr_4) AS addr_key,
      nullif(concat_ws(' | ', c.cor_addr_1, c.cor_addr_2, c.cor_addr_3,
                       c.cor_addr_4, c.cor_addr_5), '') AS raw_addr,
      year(cf.filing_dt) AS filing_year,
      cf.use_af_in::INT AS use1a,
      (coalesce(mf.coined_token_max, 0) >= {COINED_THRESHOLD})::INT AS coined,
      ow.cc AS cc,
      ow.applicant AS applicant,
      (mk.serial_no IS NOT NULL)::INT AS mkt,
      g.goods_items AS goods_items
    FROM {_rp(silver_build_dir, 'case_file')} cf
    LEFT JOIN {_rp(silver_build_dir, 'correspondent')} c USING (serial_no)
    LEFT JOIN read_parquet('{mf_glob}') mf USING (serial_no)
    LEFT JOIN owner_pick ow ON ow.serial_no = cf.serial_no
    LEFT JOIN marketplace mk ON mk.serial_no = cf.serial_no
    LEFT JOIN goods g ON g.serial_no = cf.serial_no
    WHERE cf.filing_dt >= DATE '2015-01-01'
      AND (cf.attorney_name IS NOT NULL OR c.cor_addr_2 IS NOT NULL)
    """)


def signal_rows(con: duckdb.DuckDBPyConnection) -> list[Row]:
    return con.execute(
        "SELECT serial_no, addr_key, canon_name FROM base").fetchall()


def sample_operations(
    sizes: dict[str, int],
    strata: tuple[tuple[int, int | None, int], ...] = SAMPLE_STRATA,
    forced_largest: int = FORCED_LARGEST,
) -> list[str]:
    """Deterministic size-stratified sample of operation keys. Within each
    stratum, operations order by the sha1 of their key - a content hash,
    not an RNG, so the same build always yields the same sample (ADR
    0013). The largest operations overall are always included."""
    def h(key: str) -> str:
        return hashlib.sha1(key.encode()).hexdigest()

    picked = sorted(sizes, key=lambda k: (-sizes[k], k))[:forced_largest]
    for lo, hi, quota in strata:
        taken = set(picked)
        members = sorted(
            (k for k, n in sizes.items()
             if n >= lo and (hi is None or n < hi) and k not in taken),
            key=h)
        picked.extend(members[:quota])
    return picked


def _register_assignment(con: duckdb.DuckDBPyConnection,
                         comp: dict[str, str]) -> None:
    tbl = pa.table({"serial_no": pa.array(comp.keys(), pa.string()),
                    "op_key": pa.array(comp.values(), pa.string())})
    con.register("asg", tbl)


def _member_values(con: duckdb.DuckDBPyConnection, column: str,
                   top: int) -> dict[str, list[dict[str, object]]]:
    """Per-op distinct values of one base column with counts, most common
    first, capped at `top` per operation. The cap is pushed into the
    engine with QUALIFY so a large operation does not ship all its
    distinct values back only to drop most of them."""
    rows = con.execute(f"""
      SELECT a.op_key, b.{column} AS value, count(*) AS n
      FROM asg a JOIN base b USING (serial_no)
      WHERE b.{column} IS NOT NULL
      GROUP BY 1, 2
      QUALIFY row_number() OVER (PARTITION BY a.op_key
              ORDER BY count(*) DESC, b.{column}) <= {top}
      ORDER BY a.op_key, n DESC, value
    """).fetchall()
    out: dict[str, list[dict[str, object]]] = {}
    for op_key, value, n in rows:
        out.setdefault(op_key, []).append({"value": value, "n": n})
    return out


def build_audit_sample(silver_build_dir: Path, gold_root: Path,
                       con: duckdb.DuckDBPyConnection | None = None,
                       rows: list[Row] | None = None, log=print) -> Path:
    """Write the manual-review audit sample under
    gold_root/resolution_audit_v{VERSION}/: audit_sample.json (full
    evidence per sampled operation) and review.html (the local review
    app). Local-only (ADR 0008): the evidence contains attorney names.

    Pass an existing `con` (with `base` already materialized by load_base)
    and its `rows` to share the corpus scan with `sensitivity`; otherwise a
    private connection is opened and closed here."""
    own = con is None
    if con is None:
        con = duckdb.connect()
        load_base(con, silver_build_dir, gold_root)
    if rows is None:
        rows = signal_rows(con)
    comp = variant_keys(rows, "shipped")
    sizes = Counter(comp.values())
    profiled = {k: n for k, n in sizes.items() if n >= _MIN_MARKS}
    sample = sample_operations(profiled)
    keep = set(sample)
    log(f"[audit] {len(profiled):,} profiled operations -> "
        f"{len(sample)} sampled for manual review")

    _register_assignment(
        con, {s: k for s, k in comp.items() if k in keep})
    summary = {
        r[0]: r[1:] for r in con.execute("""
          SELECT a.op_key, count(*), min(b.filing_year), max(b.filing_year),
                 count(DISTINCT b.raw_name), count(DISTINCT b.raw_addr),
                 count(DISTINCT b.applicant)
          FROM asg a JOIN base b USING (serial_no) GROUP BY 1
        """).fetchall()}
    names = _member_values(con, "raw_name", top=15)
    addrs = _member_values(con, "raw_addr", top=15)
    owners = _member_values(con, "applicant", top=10)
    countries = _member_values(con, "cc", top=5)
    con.unregister("asg")
    if own:
        con.close()

    operations = []
    for i, key in enumerate(sample, start=1):
        n, y0, y1, n_names, n_addrs, n_applicants = summary[key]
        operations.append({
            "op_id": i,
            "key_type": "name" if key.startswith("N:") else "address",
            "op_key": key,
            "n_marks": n,
            "first_year": y0,
            "last_year": y1,
            "n_name_spellings": n_names,
            "n_addresses": n_addrs,
            "n_applicants": n_applicants,
            "attorney_names": names.get(key, []),
            "addresses": addrs.get(key, []),
            "applicants": owners.get(key, []),
            "owner_countries": countries.get(key, []),
        })

    out_dir = gold_root / AUDIT_TABLE
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "table": AUDIT_TABLE,
        "version": VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "review_guide": REVIEW_GUIDE,
        "strata": [{"min_marks": lo, "max_marks_exclusive": hi, "quota": q}
                   for lo, hi, q in SAMPLE_STRATA],
        "forced_largest": FORCED_LARGEST,
        "profiled_operations": len(profiled),
        "sampled_operations": len(sample),
        "scope": "local-only; contains attorney names; never published "
                 "(ADR 0008).",
        "operations": operations,
    }
    (out_dir / "audit_sample.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")

    (out_dir / REVIEW_APP).write_text(render_review_app(doc), encoding="utf-8")
    log(f"[audit] sample + review app -> {out_dir}")
    log(f"[audit] open {(out_dir / REVIEW_APP)} in a browser to review; "
        "it embeds names, so keep it local (do not publish/commit)")
    return out_dir


def render_review_app(doc: dict) -> str:
    """Self-contained HTML review tool with the sample's evidence inlined
    (no server, no fetch - just open the file). Records verdicts in
    localStorage keyed by build and exports/imports a name-free
    verdicts.json. Embeds attorney names, so the file is local-only."""
    payload = json.dumps({
        "silver_build": doc["silver_build"],
        "version": doc["version"],
        "review_guide": doc["review_guide"],
        "operations": doc["operations"],
    })
    # The payload is inlined into a <script> block, so any evidence value
    # containing '</script>' (USPTO free-text can) would close the element
    # early and break the app. Escaping these to \uXXXX keeps the JSON
    # string values intact while making them inert in HTML/JS; U+2028/2029
    # are valid JSON but illegal in JS string literals.
    backslash = chr(92)
    for ch in "<>&" + chr(0x2028) + chr(0x2029):
        rep = backslash + f"u{ord(ch):04x}"
        payload = payload.replace(ch, rep)
    # Sentinel replace (not str.format): the template is full of literal
    # braces from CSS/JS.
    return _REVIEW_HTML.replace("/*__PAYLOAD__*/", payload)


def _profile_variant(con: duckdb.DuckDBPyConnection, variant: str,
                     comp: dict[str, str]) -> None:
    """Aggregate one variant's assignment into ops_<variant> (per-operation
    profile stats + composite score, same weights as operation_profile)
    and fs_<variant> (per-filing score + top-decile flag)."""
    _register_assignment(con, comp)
    weighted = operation_profile.weighted_score_expr()
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE ops_{variant} AS
      SELECT *, round({weighted}, 4) AS score FROM (
        SELECT a.op_key,
          count(*) AS n_marks,
          count(DISTINCT b.applicant)*1.0/count(*) AS owner_turnover,
          avg(b.coined) AS coined_rate,
          avg(b.mkt) AS marketplace_rate,
          avg(b.use1a) AS use1a_rate,
          avg((b.cc = 'CN')::INT) AS pct_cn,
          least(avg(b.goods_items)/20.0, 1.0) AS goods_chaos
        FROM asg a JOIN base b USING (serial_no)
        GROUP BY a.op_key
        HAVING count(*) >= {_MIN_MARKS})
    """)
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE fs_{variant} AS
      SELECT a.serial_no, o.score,
             o.score >= (SELECT quantile_cont(score, 0.9)
                         FROM ops_{variant}) AS top_decile
      FROM asg a JOIN ops_{variant} o USING (op_key)
    """)
    con.unregister("asg")


def sensitivity(silver_build_dir: Path, gold_root: Path,
                con: duckdb.DuckDBPyConnection | None = None,
                rows: list[Row] | None = None, log=print) -> dict:
    """Regroup under every keying variant, re-derive the operation
    profiles, and measure how the headline statistics and the per-filing
    ranking move relative to the shipped grouping. Writes
    gold_root/resolution_sensitivity.json and returns the dict.

    Pass an existing `con` (with `base` materialized) and its `rows` to
    share the corpus scan with `build_audit_sample`; otherwise a private
    connection is opened and closed here."""
    own = con is None
    if con is None:
        con = duckdb.connect()
        load_base(con, silver_build_dir, gold_root)
    if rows is None:
        rows = signal_rows(con)
    log(f"[sensitivity] {len(rows):,} attributable filings; "
        f"{len(VARIANTS)} keying variants")

    report: dict = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "min_marks": _MIN_MARKS,
        "weights": _WEIGHTS,
        "variants": {},
    }
    # "shipped" first: every other variant's vs_shipped comparison joins the
    # fs_shipped temp table, so it must be built before them regardless of
    # how VARIANTS is later ordered.
    ordered = ["shipped", *(v for v in VARIANTS if v != "shipped")]
    for variant in ordered:
        comp = variant_keys(rows, variant)
        _profile_variant(con, variant, comp)
        n_ops, profiled, max_op, p50, p90, hi_mass = one_row(con, f"""
          SELECT count(*), coalesce(sum(n_marks), 0),
                 coalesce(max(n_marks), 0),
                 round(quantile_cont(score, 0.5), 4),
                 round(quantile_cont(score, 0.9), 4),
                 coalesce(sum(n_marks) FILTER (WHERE score >= 0.5), 0)
          FROM ops_{variant}
        """)
        top_marks, top_coined, top_cn, top_turnover = one_row(con, f"""
          SELECT coalesce(sum(n_marks), 0), round(avg(coined_rate), 4),
                 round(avg(pct_cn), 4), round(avg(owner_turnover), 4)
          FROM (SELECT * FROM ops_{variant}
                ORDER BY n_marks DESC, op_key LIMIT 100)
        """)
        entry: dict = {
            "attributable_filings": len(comp),
            "n_operations": n_ops,
            "filings_profiled": profiled,
            "max_op_size": max_op,
            "op_score_p50": p50,
            "op_score_p90": p90,
            "filings_in_ops_score_ge_050": hi_mass,
            "top_100_ops_by_size": {
                "filings": top_marks,
                "mean_coined_rate": top_coined,
                "mean_pct_cn": top_cn,
                "mean_owner_turnover": top_turnover,
            },
        }
        if variant != "shipped":
            spearman, shared = one_row(con, f"""
              SELECT round(corr(ra, rb), 4), count(*)
              FROM (SELECT rank() OVER (ORDER BY sa) AS ra,
                           rank() OVER (ORDER BY sb) AS rb
                    FROM (SELECT a.score AS sa, b.score AS sb
                          FROM fs_shipped a
                          JOIN fs_{variant} b USING (serial_no)))
            """)
            jaccard = one_row(con, f"""
              SELECT round(
                sum((coalesce(a.top_decile, false)
                     AND coalesce(b.top_decile, false))::INT)*1.0
                / nullif(sum((coalesce(a.top_decile, false)
                              OR coalesce(b.top_decile, false))::INT), 0), 4)
              FROM fs_shipped a FULL JOIN fs_{variant} b USING (serial_no)
            """)[0]
            entry["vs_shipped"] = {
                "filing_score_spearman": spearman,
                "shared_filings": shared,
                "top_decile_filing_jaccard": jaccard,
            }
        report["variants"][variant] = entry
        log(f"[sensitivity] {variant}: {n_ops:,} ops, "
            f"{profiled:,} filings profiled, max op {max_op:,}")
    if own:
        con.close()

    out = gold_root / SENSITIVITY_FILE
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"[sensitivity] report -> {out}")
    return report


_COLOCATION_NOTE = (
    "Co-location is a weak, high-false-positive signal: shared offices, "
    "coworking spaces, and registered-agent addresses are common and "
    "entirely ordinary. This list flags non-hub correspondent addresses "
    "hosting two or more distinct non-hub attorney names with peculiar-"
    "looking portfolios, for HUMAN INSPECTION only. It merges no operations "
    "and asserts no coordination or wrongdoing about any party (ADR 0011, "
    "ADR 0008). A high rank is a reason to look, not a conclusion."
)


def colocation(silver_build_dir: Path, gold_root: Path,
               con: duckdb.DuckDBPyConnection | None = None,
               log=print) -> dict:
    """Address-level co-location diagnostic (ADR 0014). Ranks non-hub
    correspondent addresses that host two or more distinct non-hub attorney
    names whose here-filed portfolios score peculiar, as an inspection
    list - never a merge and never a finding (see _COLOCATION_NOTE). The
    grouping is untouched: this only reads `base`. Local-only (ADR 0008):
    the output carries attorney names and is never published. Writes
    gold_root/resolution_colocation.json.

    Pass an existing `con` (with `base` materialized) to share the corpus
    scan; otherwise a private connection is opened and closed here."""
    own = con is None
    if con is None:
        con = duckdb.connect()
        load_base(con, silver_build_dir, gold_root)

    weighted = operation_profile.weighted_score_expr()
    # Per (address, name) portfolio, restricted to non-hub names at non-hub
    # addresses - the shared-mailbox / filing-service cases are excluded by
    # the same hub caps the grouping uses. max() keeps picks deterministic.
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE _co_grp AS
      WITH name_hub AS (
        SELECT canon_name FROM base
        WHERE canon_name IS NOT NULL AND addr_key IS NOT NULL
        GROUP BY 1
        HAVING count(DISTINCT addr_key) > {entity_resolution.NAME_HUB_MAX_ADDRESSES}),
      addr_hub AS (
        SELECT addr_key FROM base
        WHERE canon_name IS NOT NULL AND addr_key IS NOT NULL
        GROUP BY 1
        HAVING count(DISTINCT canon_name) > {entity_resolution.ADDR_HUB_MAX_NAMES}),
      grp AS (
        SELECT b.addr_key, b.canon_name,
          max(b.raw_addr) AS raw_addr,
          count(*) AS n_filings,
          count(DISTINCT b.applicant)*1.0/count(*) AS owner_turnover,
          avg(b.coined) AS coined_rate,
          avg(b.mkt) AS marketplace_rate,
          avg(b.use1a) AS use1a_rate,
          avg((b.cc='CN')::INT) AS pct_cn,
          least(avg(b.goods_items)/20.0, 1.0) AS goods_chaos
        FROM base b
        WHERE b.addr_key IS NOT NULL AND b.canon_name IS NOT NULL
          AND b.canon_name NOT IN (SELECT canon_name FROM name_hub)
          AND b.addr_key NOT IN (SELECT addr_key FROM addr_hub)
        GROUP BY 1, 2
        HAVING count(*) >= {CO_MIN_FILINGS_PER_NAME})
      SELECT addr_key, canon_name, raw_addr, n_filings,
             round({weighted}, 4) AS score
      FROM grp
    """)
    p75, p90 = one_row(
        con, "SELECT quantile_cont(score, 0.75), quantile_cont(score, 0.9) "
             "FROM _co_grp")
    # No qualifying groups -> sentinels leave the inspection list empty.
    p75 = p75 if p75 is not None else 1e9
    p90 = p90 if p90 is not None else 1e9

    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE _co_addr AS
      SELECT addr_key, max(raw_addr) AS raw_addr,
        count(*) AS n_distinct_names,
        sum(n_filings) AS n_filings,
        sum((score >= {p90})::INT) AS n_names_peculiar,
        round(max(score), 4) AS max_score,
        round(avg(score), 4) AS mean_score
      FROM _co_grp GROUP BY addr_key
      HAVING count(*) >= 2 AND max(score) >= {p75}
    """)
    qualifying = scalar(con, "SELECT count(*) FROM _co_addr")
    top = con.execute(f"""
      SELECT addr_key, raw_addr, n_distinct_names, n_filings,
             n_names_peculiar, max_score, mean_score
      FROM _co_addr
      ORDER BY n_names_peculiar DESC, mean_score DESC, n_filings DESC, addr_key
      LIMIT {CO_REPORT_CAP}
    """).fetchall()

    names_by_addr: dict[str, list[dict[str, object]]] = {}
    keys = [r[0] for r in top]
    if keys:
        con.register("_co_keys", pa.table({"addr_key": pa.array(keys, pa.string())}))
        for addr, name, n, sc in con.execute(f"""
          SELECT g.addr_key, g.canon_name, g.n_filings, g.score
          FROM _co_grp g JOIN _co_keys k USING (addr_key)
          QUALIFY row_number() OVER (PARTITION BY g.addr_key
                  ORDER BY g.score DESC, g.canon_name) <= {CO_NAMES_PER_ADDR}
          ORDER BY g.addr_key, g.score DESC, g.canon_name
        """).fetchall():
            names_by_addr.setdefault(addr, []).append(
                {"name": name, "n_filings": n, "score": sc})
        con.unregister("_co_keys")
    if own:
        con.close()

    addresses = [{
        "addr_key": r[0], "raw_addr": r[1], "n_distinct_names": r[2],
        "n_filings": r[3], "n_names_peculiar": r[4], "max_score": r[5],
        "mean_score": r[6], "names": names_by_addr.get(r[0], []),
    } for r in top]
    report = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": silver_build_dir.name,
        "note": _COLOCATION_NOTE,
        "params": {
            "name_hub_max_addresses": entity_resolution.NAME_HUB_MAX_ADDRESSES,
            "addr_hub_max_names": entity_resolution.ADDR_HUB_MAX_NAMES,
            "min_filings_per_name": CO_MIN_FILINGS_PER_NAME,
            "weights": _WEIGHTS,
            "score_p75": round(float(p75), 4) if p75 < 1e9 else None,
            "peculiar_threshold_p90": round(float(p90), 4) if p90 < 1e9 else None,
        },
        "scope": "local-only; contains attorney names; never published "
                 "(ADR 0008).",
        "qualifying_addresses": qualifying,
        "reported": len(addresses),
        "addresses": addresses,
    }
    out = gold_root / COLOCATION_FILE
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"[colocation] {qualifying:,} candidate addresses "
        f"(reporting top {len(addresses):,}) -> {out}")
    if qualifying > len(addresses):
        log(f"[colocation] NOTE: {qualifying - len(addresses):,} qualifying "
            "addresses beyond the report cap are omitted from the list")
    return report


def _stratum_label(n_marks: int) -> str:
    for lo, hi, _ in SAMPLE_STRATA:
        if n_marks >= lo and (hi is None or n_marks < hi):
            return f"{lo}-{hi - 1}" if hi else f"{lo}+"
    return "below_min"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n. Behaves at the
    n=0 and p in {0,1} edges where the normal approximation does not."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def _precision(coherent: int, mixed: int) -> dict:
    adjudicated = coherent + mixed
    lo, hi = wilson(coherent, adjudicated)
    return {
        "coherent": coherent,
        "mixed": mixed,
        "adjudicated": adjudicated,
        "precision": round(coherent / adjudicated, 4) if adjudicated else None,
        "wilson95": [lo, hi],
    }


def score_verdicts(gold_root: Path, verdicts_path: Path, log=print) -> dict:
    """Turn a review-app verdicts.json into a precision estimate (overall
    and per size stratum, with Wilson 95% intervals). Precision counts only
    over-merge as an error: coherent / (coherent + mixed); `unclear` and
    un-reviewed operations are excluded from the denominator (ADR 0014).
    The current audit_sample.json supplies the authoritative op sizes and a
    drift check. Writes <verdicts_dir>/resolution_precision.json."""
    vpath = Path(verdicts_path)
    doc = json.loads(vpath.read_text(encoding="utf-8"))
    sample_path = gold_root / AUDIT_TABLE / "audit_sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sizes = {o["op_id"]: o["n_marks"] for o in sample["operations"]}

    warnings: list[str] = []
    if doc.get("silver_build") != sample["silver_build"]:
        warnings.append(
            f"verdicts were made against build {doc.get('silver_build')!r} but "
            f"the current sample is {sample['silver_build']!r}; op ids may not "
            "line up - regenerate the sample or re-review")

    counts = Counter()
    strata: dict[str, list[int]] = {}  # label -> [coherent, mixed]
    overall = [0, 0]
    for entry in doc.get("verdicts", []):
        op_id = entry.get("op_id")
        verdict = (entry.get("verdict") or "").strip().lower()
        if not verdict:
            counts["unreviewed"] += 1
            continue
        if verdict not in VERDICTS:
            warnings.append(f"op {op_id}: unrecognized verdict {verdict!r}")
            counts["invalid"] += 1
            continue
        if op_id not in sizes:
            # a verdict for an op absent from the current sample must not
            # inflate the reviewed-count or precision figures
            warnings.append(f"op {op_id}: not in the current sample - skipped")
            counts["stale"] += 1
            continue
        counts[verdict] += 1
        if "n_marks" in entry and entry["n_marks"] != sizes[op_id]:
            warnings.append(
                f"op {op_id}: n_marks {entry['n_marks']} != current "
                f"{sizes[op_id]} (sample changed under the verdicts)")
        if verdict == "unclear":
            continue
        bucket = strata.setdefault(_stratum_label(sizes[op_id]), [0, 0])
        col = 0 if verdict == "coherent" else 1
        bucket[col] += 1
        overall[col] += 1

    report = {
        "scored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "silver_build": sample["silver_build"],
        "verdicts_file": vpath.name,
        "sampled_operations": len(sizes),
        "counts": {
            "coherent": counts["coherent"],
            "mixed": counts["mixed"],
            "unclear": counts["unclear"],
            "unreviewed": counts["unreviewed"],
            "invalid": counts["invalid"],
            "stale": counts["stale"],
        },
        "overall": _precision(*overall),
        "by_stratum": {label: _precision(c, m)
                       for label, (c, m) in sorted(strata.items())},
        "definition": "precision = coherent / (coherent + mixed); over-merge "
                      "is the only error (ADR 0014). Prevalence is not "
                      "estimated - this is grouping precision on a "
                      "size-stratified sample.",
        "warnings": warnings,
    }
    out = vpath.parent / "resolution_precision.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    reviewed = counts["coherent"] + counts["mixed"] + counts["unclear"]
    log(f"[precision] {reviewed}/{len(sizes)} reviewed; "
        f"overall {report['overall']['precision']} "
        f"(95% CI {report['overall']['wilson95']}) -> {out}")
    for w in warnings:
        log(f"[precision] WARNING: {w}")
    return report


# Self-contained local review UI. Opened straight from disk (file://): the
# sample's evidence is inlined at generation time, so there is no fetch and
# no server. Verdicts live in localStorage and export/import as a name-free
# verdicts.json. The page embeds attorney names, so it is local-only.
_REVIEW_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Operation grouping - precision audit</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.55 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0; background: Canvas; color: CanvasText; }
  header { position: sticky; top: 0; z-index: 5; padding: 10px 16px;
           background: Canvas; border-bottom: 1px solid #8884;
           display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; }
  header .grow { flex: 1 1 auto; }
  .pill { padding: 2px 8px; border-radius: 10px; border: 1px solid #8886;
          font-variant-numeric: tabular-nums; }
  .bar { height: 6px; width: 160px; background: #8883; border-radius: 3px;
         overflow: hidden; }
  .bar > i { display: block; height: 100%; background: #4a90d9; width: 0; }
  button { font: inherit; padding: 5px 10px; border-radius: 6px;
           border: 1px solid #8886; background: #8881; color: inherit;
           cursor: pointer; }
  button:hover { background: #8883; }
  main { max-width: 900px; margin: 0 auto; padding: 18px 16px 120px; }
  h2 { margin: 0 0 2px; }
  .sub { color: GrayText; margin-bottom: 14px; }
  .facts { display: flex; flex-wrap: wrap; gap: 6px 8px; margin-bottom: 16px; }
  section.ev { margin: 14px 0; }
  section.ev h3 { margin: 0 0 6px; font-size: 13px; text-transform: uppercase;
                  letter-spacing: .04em; color: GrayText; }
  table { border-collapse: collapse; width: 100%; }
  td { padding: 3px 8px; border-bottom: 1px solid #8883; vertical-align: top; }
  td.n { text-align: right; width: 70px; color: GrayText;
         font-variant-numeric: tabular-nums; white-space: nowrap; }
  .val { word-break: break-word; }
  .verdicts { display: flex; gap: 8px; margin: 18px 0 8px; }
  .verdicts button { padding: 8px 14px; font-weight: 600; }
  .v-coherent.on { background: #2e7d3233; border-color: #2e7d32; }
  .v-mixed.on { background: #c6282833; border-color: #c62828; }
  .v-unclear.on { background: #f9a82533; border-color: #f9a825; }
  textarea { width: 100%; min-height: 60px; font: inherit; padding: 8px;
             border-radius: 6px; border: 1px solid #8886; background: #8881;
             color: inherit; }
  .hint { color: GrayText; font-size: 12px; margin-top: 6px; }
  kbd { border: 1px solid #8886; border-bottom-width: 2px; border-radius: 4px;
        padding: 0 5px; font: 11px monospace; }
  footer { position: fixed; bottom: 0; left: 0; right: 0; padding: 8px 16px;
           background: Canvas; border-top: 1px solid #8884; display: flex;
           gap: 10px; align-items: center; }
</style>
</head>
<body>
<header>
  <strong>Grouping precision audit</strong>
  <span class="pill" id="pos"></span>
  <span class="bar"><i id="barfill"></i></span>
  <span class="pill" id="tally"></span>
  <span class="pill" id="prec"></span>
  <span class="grow"></span>
  <button id="jump" title="Jump to next unreviewed (j)">Next unreviewed</button>
  <button id="imp">Import verdicts</button>
  <button id="dl">Download verdicts</button>
  <input type="file" id="file" accept="application/json" hidden>
</header>
<main id="view"></main>
<footer>
  <button id="prev">&larr; Prev</button>
  <button id="next">Next &rarr;</button>
  <span class="hint">Keys: <kbd>c</kbd> coherent &middot; <kbd>m</kbd> mixed
    &middot; <kbd>u</kbd> unclear &middot; <kbd>&larr;</kbd>/<kbd>&rarr;</kbd>
    move &middot; <kbd>j</kbd> next unreviewed &middot; <kbd>s</kbd> save</span>
  <span class="grow"></span>
  <span class="hint" id="save-hint"></span>
</footer>
<script>
const DATA = /*__PAYLOAD__*/;
const OPS = DATA.operations.slice().sort((a, b) => b.n_marks - a.n_marks);
const KEY = 'resaudit:' + DATA.silver_build;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { state = {}; }
let i = 0;

const esc = s => String(s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const el = id => document.getElementById(id);

function persist() { localStorage.setItem(KEY, JSON.stringify(state)); }

function evTable(title, rows) {
  if (!rows || !rows.length) return '';
  const body = rows.map(r =>
    '<tr><td class="val">' + esc(r.value) + '</td><td class="n">' +
    r.n.toLocaleString() + '</td></tr>').join('');
  return '<section class="ev"><h3>' + title + '</h3><table>' + body +
         '</table></section>';
}

function render() {
  const o = OPS[i];
  const s = state[o.op_id] || {};
  const facts = [
    ['filings', o.n_marks.toLocaleString()],
    ['keyed on', o.key_type],
    ['years', o.first_year + '-' + o.last_year],
    ['name spellings', o.n_name_spellings],
    ['addresses', o.n_addresses],
    ['distinct applicants', o.n_applicants],
  ].map(f => '<span class="pill">' + f[0] + ': <b>' + f[1] + '</b></span>').join('');
  el('view').innerHTML =
    '<h2>Operation ' + (i + 1) + ' of ' + OPS.length + '</h2>' +
    '<div class="sub">op_id ' + o.op_id + ' &middot; the question: could these ' +
    'filings plausibly be one filing operation? Only a blend of clearly ' +
    'unrelated filers (over-merge) counts as an error - one filer split ' +
    'across groups does not.</div>' +
    '<div class="facts">' + facts + '</div>' +
    evTable('Attorney-name spellings', o.attorney_names) +
    evTable('Correspondent addresses', o.addresses) +
    evTable('Applicants (sample)', o.applicants) +
    evTable('Owner countries', o.owner_countries) +
    '<div class="verdicts">' +
      '<button class="v-coherent' + (s.verdict === 'coherent' ? ' on' : '') +
        '" data-v="coherent">Coherent (c)</button>' +
      '<button class="v-mixed' + (s.verdict === 'mixed' ? ' on' : '') +
        '" data-v="mixed">Mixed / over-merge (m)</button>' +
      '<button class="v-unclear' + (s.verdict === 'unclear' ? ' on' : '') +
        '" data-v="unclear">Unclear (u)</button>' +
    '</div>' +
    '<textarea id="notes" placeholder="Scratch notes for your own review ' +
    '(stay in this browser, never exported or committed).">' +
    esc(s.notes || '') + '</textarea>' +
    '<div class="hint">Verdicts autosave in this browser; use ' +
    '<b>Download verdicts</b> for the durable, committable copy. Notes are ' +
    'local scratch and are not exported.</div>';
  document.querySelectorAll('.verdicts button').forEach(b =>
    b.onclick = () => setVerdict(b.dataset.v));
  el('notes').oninput = e => {
    (state[o.op_id] = state[o.op_id] || {}).notes = e.target.value;
    persist();
  };
  stats();
}

function setVerdict(v) {
  const o = OPS[i];
  const cur = state[o.op_id] || {};
  cur.verdict = cur.verdict === v ? '' : v;
  state[o.op_id] = cur;
  persist();
  render();
}

function stats() {
  let c = 0, m = 0, u = 0;
  for (const o of OPS) {
    const v = (state[o.op_id] || {}).verdict;
    if (v === 'coherent') c++; else if (v === 'mixed') m++;
    else if (v === 'unclear') u++;
  }
  const reviewed = c + m + u;
  el('pos').textContent = 'op ' + (i + 1) + '/' + OPS.length;
  el('barfill').style.width = (100 * reviewed / OPS.length) + '%';
  el('tally').textContent = reviewed + '/' + OPS.length + ' reviewed  ' +
    '(' + c + ' coherent, ' + m + ' mixed, ' + u + ' unclear)';
  const denom = c + m;
  el('prec').textContent = denom
    ? 'precision ' + (c / denom).toFixed(3) + ' (n=' + denom + ')'
    : 'precision -';
}

function go(d) { i = (i + d + OPS.length) % OPS.length; render(); }

function jumpUnreviewed() {
  for (let k = 1; k <= OPS.length; k++) {
    const j = (i + k) % OPS.length;
    if (!((state[OPS[j].op_id] || {}).verdict)) { i = j; render(); return; }
  }
  el('save-hint').textContent = 'all operations reviewed';
}

function download() {
  // verdicts.json is committed, so it carries only structured, name-free
  // fields - never the free-text notes, which stay in this browser.
  const verdicts = OPS.map(o => ({
    op_id: o.op_id, n_marks: o.n_marks, key_type: o.key_type,
    verdict: (state[o.op_id] || {}).verdict || '',
  }));
  const blob = new Blob([JSON.stringify(
    { silver_build: DATA.silver_build, audit_table_version: DATA.version,
      verdicts }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'verdicts.json';
  a.click();
  URL.revokeObjectURL(a.href);
  el('save-hint').textContent = 'downloaded verdicts.json';
}

function importFile(ev) {
  const f = ev.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => {
    try {
      const doc = JSON.parse(r.result);
      for (const v of doc.verdicts || []) {
        if (v.verdict || v.notes)
          state[v.op_id] = { verdict: v.verdict || '', notes: v.notes || '' };
      }
      persist();
      render();
      el('save-hint').textContent = 'imported ' + (doc.verdicts || []).length +
        ' rows';
    } catch (e) { el('save-hint').textContent = 'import failed: ' + e.message; }
  };
  r.readAsText(f);
}

el('prev').onclick = () => go(-1);
el('next').onclick = () => go(1);
el('jump').onclick = jumpUnreviewed;
el('dl').onclick = download;
el('imp').onclick = () => el('file').click();
el('file').onchange = importFile;
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === 'c' || k === 'm' || k === 'u')
    setVerdict({ c: 'coherent', m: 'mixed', u: 'unclear' }[k]);
  else if (e.key === 'ArrowRight' || k === 'n') go(1);
  else if (e.key === 'ArrowLeft' || k === 'p') go(-1);
  else if (k === 'j') jumpUnreviewed();
  else if (k === 's') download();
  else return;
  e.preventDefault();
});
render();
</script>
</body>
</html>
"""
