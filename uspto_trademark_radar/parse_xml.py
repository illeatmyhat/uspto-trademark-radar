"""Streaming parser: one source zip -> per-table Parquet parts.

- lxml iterparse over each XML member of the zip (never DOM-loads a file).
- Hard-stops on unknown/missing DTD version (silently mis-parsed
  silver is worse than a failed build).
- Output directory is complete iff `_manifest.json` exists (written last), so
  interrupted parses are invisible to the build and simply re-run.

XML paths follow the US Trademark Applications v2.0 DTD; all are confirmed
against live data, the product XML documentation, and the OCE 2023 variable
tables (docs/silver-schema.md §7). Where an element legitimately appears in
more than one place, `_first()` tries the candidates in order.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

from .config import PARSER_VERSION, SUPPORTED_DTD_VERSIONS
from .schema import TABLES


class DtdVersionError(RuntimeError):
    """File declares a DTD version the parser has not been validated against."""


# -- low-level extraction helpers ------------------------------------------


def _text(elem: etree._Element | None, path: str) -> str | None:
    if elem is None:
        return None
    val = elem.findtext(path)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _first(elem: etree._Element | None, *paths: str) -> str | None:
    """First non-empty text among candidate paths."""
    for p in paths:
        v = _text(elem, p)
        if v is not None:
            return v
    return None


def _date(raw: str | None) -> date | None:
    """YYYYMMDD -> date; all-zero / month-or-day-zero sentinels -> None."""
    if not raw or len(raw) != 8 or not raw.isdigit() or raw == "00000000":
        return None
    y, m, d = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
    try:
        return date(y, m, d)
    except ValueError:
        # Real data contains partial dates like 19870000; keep silver clean.
        return None


def _bool(raw: str | None) -> bool | None:
    if raw == "T":
        return True
    if raw == "F":
        return False
    return None


def _serial(raw: str | None) -> str | None:
    return raw.zfill(8) if raw else None


def _reg_no(raw: str | None) -> str | None:
    # "0000000" means not registered.
    return None if not raw or raw.strip("0") == "" else raw


def _int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


# -- per-case-file extraction -------------------------------------------------


@dataclass
class Rows:
    """One batch of extracted rows, keyed by silver table name."""
    by_table: dict[str, list[dict]] = field(
        default_factory=lambda: {t: [] for t in TABLES}
    )

    def add(self, table: str, row: dict) -> None:
        self.by_table[table].append(row)


def extract_case_file(cf: etree._Element, rows: Rows) -> None:
    serial = _serial(_text(cf, "serial-number"))
    if not serial:
        return  # a case-file without a serial number is unusable
    hdr = cf.find("case-file-header")
    # Madrid IR state lives in its own <international-registration> group,
    # a sibling of the header (confirmed against live dailies 2026-07-08).
    ir = cf.find("international-registration")

    rows.add("case_file", {
        "serial_no": serial,
        "registration_no": _reg_no(_text(cf, "registration-number")),
        "mark_id_char": _text(hdr, "mark-identification"),
        "filing_dt": _date(_text(hdr, "filing-date")),
        "registration_dt": _date(_text(hdr, "registration-date")),
        "publication_dt": _date(_text(hdr, "published-for-opposition-date")),
        "abandon_dt": _date(_text(hdr, "abandonment-date")),
        "cancel_cd": _text(hdr, "cancellation-code"),
        "cancel_dt": _date(_text(hdr, "cancellation-date")),  # confirmed live
        "cancel_pend_in": _bool(_text(hdr, "cancellation-pending-in")),
        "renewal_dt": _date(_text(hdr, "renewal-date")),
        "renewal_file_in": _bool(_text(hdr, "renewal-filed-in")),
        "status_cd": _text(hdr, "status-code"),
        "status_dt": _date(_text(hdr, "status-date")),
        "mark_draw_cd": _text(hdr, "mark-drawing-code"),
        "std_char_claimed_in": _bool(_text(hdr, "standard-characters-claimed-in")),
        "attorney_name": _text(hdr, "attorney-name"),
        "atty_dkt_no": _text(hdr, "attorney-docket-number"),
        "domestic_rep_name": _first(cf, "case-file-header/"
                                        "domestic-representative-name",
                                    "domestic-representative-name"),
        # Confirmed against live data 2026-07-08 (2000-case-file inventory
        # of apc260102.zip): the examiner is "employee-name" in v2.0.
        "exm_name": _text(hdr, "employee-name"),
        "exm_office_cd": _text(hdr, "law-office-assigned-location-code"),
        "file_location": _text(hdr, "current-location"),
        "file_location_dt": _date(_text(hdr, "location-date")),
        # Filing-basis *current* indicators — element names confirmed against
        # live data 2026-07-08. NB "intent-to-use-current-in", not
        # "-currently-" like the use-application flag.
        "use_af_in": _bool(_text(hdr, "use-application-currently-in")),
        "use_intent_in": _bool(_text(hdr, "intent-to-use-current-in")),
        "for_app_in": _bool(_text(hdr, "filing-basis-current-44d-in")),
        "for_reg_in": _bool(_text(hdr, "filing-basis-current-44e-in")),
        "intl_reg_in": _bool(_text(hdr, "filing-basis-current-66a-in")),
        "no_basis_in": _bool(_text(hdr, "filing-current-no-basis-in")),
        # As-filed basis indicators. NB per the OCE mapping the at-filing
        # no-basis flag is the confusingly named "without-basis-currently-in".
        "use_file_in": _bool(_text(hdr, "filed-as-use-application-in")),
        "use_intent_file_in": _bool(_text(hdr, "intent-to-use-in")),
        "for_app_file_in": _bool(_text(hdr, "filing-basis-filed-as-44d-in")),
        "for_reg_file_in": _bool(_text(hdr, "filing-basis-filed-as-44e-in")),
        "intl_reg_file_in": _bool(_text(hdr, "filing-basis-filed-as-66a-in")),
        "no_basis_file_in": _bool(_text(hdr, "without-basis-currently-in")),
        "amend_reg_dt": _date(_text(hdr, "amend-to-register-date")),
        "amend_lb_use_in": _bool(_text(hdr, "amended-to-use-application-in")),
        "amend_lb_itu_in": _bool(_text(hdr, "amended-to-itu-application-in")),
        "amend_lb_for_app_in": _bool(_text(hdr, "amended-to-44d-application-in")),
        "amend_lb_for_reg_in": _bool(_text(hdr, "amended-to-44e-application-in")),
        "amend_principal_in": _bool(_text(hdr, "principal-register-amended-in")),
        "amend_supp_reg_in": _bool(_text(hdr, "supplemental-register-amended-in")),
        "chg_reg_in": _bool(_text(hdr, "change-registration-in")),
        "opposition_pend_in": _bool(_text(hdr, "opposition-pending-in")),
        "interference_pend_in": _bool(_text(hdr, "interference-pending-in")),
        "concur_use_in": _bool(_text(hdr, "concurrent-use-in")),
        "concur_use_pend_in": _bool(_text(hdr, "concurrent-use-proceeding-in")),
        "concur_use_pub_in": _bool(_text(hdr, "published-concurrent-in")),
        "use_afdv_file_in": _bool(_text(hdr, "section-8-filed-in")),
        "use_afdv_acc_in": _bool(_text(hdr, "section-8-accepted-in")),
        "use_afdv_par_acc_in": _bool(_text(hdr, "section-8-partial-accept-in")),
        "incontest_file_in": _bool(_text(hdr, "section-15-filed-in")),
        "incontest_ack_in": _bool(_text(hdr, "section-15-acknowledged-in")),
        "acq_dist_in": _bool(_text(hdr, "section-2f-in")),
        "acq_dist_part_in": _bool(_text(hdr, "section-2f-in-part-in")),
        "repub_12c_in": _bool(_text(hdr, "section-12c-in")),
        "repub_12c_dt": _date(_text(hdr, "republished-12c-date")),
        "draw_color_file_in": _bool(_text(hdr, "color-drawing-filed-in")),
        "draw_color_cur_in": _bool(_text(hdr, "color-drawing-current-in")),
        "draw_3d_file_in": _bool(_text(hdr, "drawing-3d-filed-in")),
        "draw_3d_cur_in": _bool(_text(hdr, "drawing-3d-current-in")),
        "for_priority_in": _bool(_text(hdr, "foreign-priority-in")),
        "ir_no": _text(ir, "international-registration-number"),
        "ir_registration_dt": _date(
            _text(ir, "international-registration-date")),
        "ir_publication_dt": _date(
            _text(ir, "international-publication-date")),
        "ir_renewal_dt": _date(_text(ir, "international-renewal-date")),
        "ir_death_dt": _date(_text(ir, "international-death-date")),
        "ir_status_cd": _text(ir, "international-status-code"),
        "ir_status_dt": _date(_text(ir, "international-status-date")),
        "ir_auto_reg_dt": _date(_text(ir, "auto-protection-date")),
        "ir_first_refus_in": _bool(_text(ir, "first-refusal-in")),
        "ir_priority_in": _bool(_text(ir, "priority-claimed-in")),
        "ir_priority_dt": _date(_text(ir, "priority-claimed-date")),
        "supp_reg_in": _bool(_text(hdr, "supplemental-register-in")),  # confirmed live
        "certification_in": _bool(_text(hdr, "certification-mark-in")),
        # v2.0 splits collective into trademark/service/membership flags;
        # OCE's collective_in maps to membership (confirmed present live).
        "collective_in": _bool(_text(hdr, "collective-membership-mark-in")),
        "coll_serv_mark_in": _bool(_text(hdr, "collective-service-mark-in")),
        "coll_trade_mark_in": _bool(_text(hdr, "collective-trademark-in")),
        "service_in": _bool(_text(hdr, "service-mark-in")),
        "trade_in": _bool(_text(hdr, "trademark-in")),
        "related_other_in": _bool(
            _text(cf, "prior-registration-applications/other-related-in")),
    })

    change_seq = 0
    for o in cf.iterfind("case-file-owners/case-file-owner"):
        rows.add("owner", {
            "serial_no": serial,
            "own_seq": _int(_text(o, "entry-number")),
            "own_type_cd": _text(o, "party-type"),
            "own_name": _text(o, "party-name"),
            "own_altn_name": _text(o, "dba-aka-text"),
            "own_entity_cd": _text(o, "legal-entity-type-code"),
            "own_entity_desc": _text(o, "entity-statement"),
            "own_composed_of": _text(o, "composed-of-statement"),
            "own_addr_1": _text(o, "address-1"),
            "own_addr_2": _text(o, "address-2"),
            "own_addr_city": _text(o, "city"),
            "own_addr_state_cd": _text(o, "state"),
            "own_addr_postal": _text(o, "postcode"),
            "own_addr_country_cd": _text(o, "country"),
            "own_addr_other_cd": _text(o, "other"),
            "own_nalty_country_cd": _text(o, "nationality/country"),  # confirmed live
            "own_nalty_state_cd": _text(o, "nationality/state"),
            "own_nalty_other_cd": _text(o, "nationality/other"),
        })
        # <name-change-explanation> occurs zero or MORE times per owner
        # (XML docs, Case File Owners section).
        for nc in o.iterfind("name-change-explanation"):
            if nc.text and nc.text.strip():
                change_seq += 1
                rows.add("owner_name_change", {
                    "serial_no": serial,
                    "change_seq": change_seq,
                    "change_text": nc.text.strip(),
                })

    for i, c in enumerate(cf.iterfind("classifications/classification"), 1):
        rows.add("classification", {
            "serial_no": serial,
            "class_seq": i,
            "primary_cd": _text(c, "primary-code"),
            "class_status_cd": _text(c, "status-code"),
            "class_status_dt": _date(_text(c, "status-date")),
            "class_intl_count": _int(_text(c, "international-code-total-no")),
            "class_us_count": _int(_text(c, "us-code-total-no")),
            "use_first_anywhere_dt": _date(_text(c, "first-use-anywhere-date")),
            "use_first_commerce_dt": _date(_text(c, "first-use-in-commerce-date")),
            "use_first_anywhere_raw": _text(c, "first-use-anywhere-date"),
            "use_first_commerce_raw": _text(c, "first-use-in-commerce-date"),
        })
        for ic in c.iterfind("international-code"):
            if ic.text and ic.text.strip():
                rows.add("intl_class", {
                    "serial_no": serial,
                    "class_seq": i,
                    "intl_class_cd": ic.text.strip().zfill(3),
                })
        for uc in c.iterfind("us-code"):
            if uc.text and uc.text.strip():
                rows.add("us_class", {
                    "serial_no": serial,
                    "class_seq": i,
                    "us_class_cd": uc.text.strip(),
                })

    for i, s in enumerate(
            cf.iterfind("case-file-statements/case-file-statement"), 1):
        rows.add("statement", {
            "serial_no": serial,
            "statement_seq": i,
            "statement_type_cd": _text(s, "type-code"),
            "statement_text": _text(s, "text"),
        })

    for e in cf.iterfind(
            "case-file-event-statements/case-file-event-statement"):
        rows.add("event", {
            "serial_no": serial,
            "event_seq": _int(_text(e, "number")),
            "event_cd": _text(e, "code"),
            "event_type_cd": _text(e, "type"),
            "event_dt": _date(_text(e, "date")),
        })

    for d in cf.iterfind("design-searches/design-search"):
        code = _text(d, "code")
        if code:
            rows.add("design_search", {
                "serial_no": serial,
                "design_search_cd": code,
            })

    for p in cf.iterfind("prior-registration-applications/"
                         "prior-registration-application"):
        rows.add("prior_mark", {
            "serial_no": serial,
            "prior_no": _text(p, "number"),
            "prior_type_cd": _text(p, "relationship-type"),
        })

    for i, f in enumerate(cf.iterfind("foreign-applications/"
                                      "foreign-application"), 1):
        rows.add("foreign_app", {
            "serial_no": serial,
            "foreign_seq": _int(_text(f, "entry-number")) or i,
            "for_country_cd": _text(f, "country"),
            "for_other": _text(f, "other"),
            "for_app_no": _text(f, "application-number"),
            "for_app_dt": _date(_text(f, "filing-date")),
            "for_reg_no": _text(f, "registration-number"),
            "for_reg_dt": _date(_text(f, "registration-date")),
            "for_reg_exp_dt": _date(_text(f, "registration-expiration-date")),
            "for_renewal_no": _text(f, "renewal-number"),
            "for_renewal_dt": _date(_text(f, "registration-renewal-date")),
            "for_renewal_exp_dt": _date(
                _text(f, "registration-renewal-expiration-date")),
            "for_priority_claim_in": _bool(
                _text(f, "foreign-priority-claim-in")),
        })

    for i, m in enumerate(cf.iterfind("madrid-international-filing-requests/"
                                      "madrid-international-filing-record"), 1):
        rows.add("madrid_intl_file", {
            "serial_no": serial,
            "madrid_seq": _int(_text(m, "entry-number")) or i,
            "madrid_ref_no": _text(m, "reference-number"),
            "madrid_orig_filing_dt": _date(
                _text(m, "original-filing-date-uspto")),
            "ir_no": _text(m, "international-registration-number"),
            "ir_dt": _date(_text(m, "international-registration-date")),
            # Confirmed against the OCE 2023 variable tables (mir_status_cd
            # <international-status-code>, mir_status_dt <...-status-date>).
            "madrid_status_cd": _text(m, "international-status-code"),
            "madrid_status_dt": _date(_text(m, "international-status-date")),
            "irregularity_reply_by_dt": _date(
                _text(m, "irregularity-reply-by-date")),
            "ir_renewal_dt": _date(_text(m, "international-renewal-date")),
        })

    cor = cf.find("correspondent")
    if cor is not None:
        rows.add("correspondent", {
            "serial_no": serial,
            **{f"cor_addr_{i}": _text(cor, f"address-{i}") for i in range(1, 6)},
        })


# -- zip -> parquet part directory ------------------------------------------


class _PartWriters:
    """Lazy per-table ParquetWriter set, filling lineage columns."""

    def __init__(self, out_dir: Path, file_dt: date, file_name: str):
        self.out_dir = out_dir
        self.file_dt = file_dt
        self.file_name = file_name
        self.writers: dict[str, pq.ParquetWriter] = {}
        self.counts: dict[str, int] = {t: 0 for t in TABLES}

    def flush(self, rows: Rows) -> None:
        for table, batch in rows.by_table.items():
            if not batch:
                continue
            for r in batch:
                r["file_dt_source"] = self.file_dt
                r["file_name_source"] = self.file_name
            tbl = pa.Table.from_pylist(batch, schema=TABLES[table])
            if table not in self.writers:
                self.writers[table] = pq.ParquetWriter(
                    self.out_dir / f"{table}.parquet", TABLES[table],
                    compression="zstd",
                )
            self.writers[table].write_table(tbl)
            self.counts[table] += len(batch)
            batch.clear()

    def close(self) -> None:
        for w in self.writers.values():
            w.close()


def parse_zip(zip_path: Path, out_root: Path, file_dt: date,
              batch_size: int = 20_000) -> dict:
    """Parse one source zip into ``out_root/<zip stem>/<table>.parquet``.

    Returns the manifest dict. Skips (returning the existing manifest) if the
    output is already complete for this parser version — parse-once cache.
    """
    out_dir = out_root / zip_path.stem
    manifest_path = out_dir / "_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("parser_version") == PARSER_VERSION:
            return manifest

    # (Re)build from scratch — a part dir without a manifest is garbage.
    if out_dir.exists():
        for p in out_dir.iterdir():
            p.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    writers = _PartWriters(out_dir, file_dt, zip_path.name)
    rows = Rows()
    n_case_files = 0
    dtd_versions: set[str] = set()

    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith(".xml")]
            if not members:
                raise DtdVersionError(
                    f"{zip_path.name}: no XML members found in zip"
                )
            for member in members:
                with zf.open(member) as fh:
                    n_case_files += _parse_stream(
                        fh, f"{zip_path.name}/{member}", writers, rows,
                        dtd_versions, batch_size,
                    )
        writers.flush(rows)
    finally:
        writers.close()

    manifest = {
        "source_zip": zip_path.name,
        "file_dt_source": file_dt.isoformat(),
        "parser_version": PARSER_VERSION,
        "dtd_versions": sorted(dtd_versions),
        "case_file_count": n_case_files,
        "row_counts": writers.counts,
    }
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(manifest_path)  # completeness marker, written last
    return manifest


def _parse_stream(fh, source_label: str, writers: _PartWriters, rows: Rows,
                  dtd_versions: set[str], batch_size: int) -> int:
    """iterparse one XML stream; returns case-file count."""
    version_seen = False
    n = 0
    context = etree.iterparse(
        fh, events=("end",), tag=("version-no", "case-file"),
        recover=False, huge_tree=True,
    )
    for _, elem in context:
        if elem.tag == "version-no":
            v = (elem.text or "").strip()
            if v not in SUPPORTED_DTD_VERSIONS:
                raise DtdVersionError(
                    f"{source_label}: DTD version '{v}' is not in the "
                    f"validated set {sorted(SUPPORTED_DTD_VERSIONS)}. "
                    "HARD STOP — validate the parser against the new DTD "
                    "(bundled in the zip), then add the version to "
                    "config.SUPPORTED_DTD_VERSIONS."
                )
            dtd_versions.add(v)
            version_seen = True
        else:  # case-file
            if not version_seen:
                raise DtdVersionError(
                    f"{source_label}: <case-file> encountered before any "
                    "<version-no> — file structure drifted; refusing to "
                    "parse unverified input."
                )
            extract_case_file(elem, rows)
            n += 1
            if n % batch_size == 0:
                writers.flush(rows)
        # Free memory: clear the element and any preceding siblings.
        elem.clear()
        parent = elem.getparent()
        if parent is not None:
            while elem.getprevious() is not None:
                del parent[0]
    return n
