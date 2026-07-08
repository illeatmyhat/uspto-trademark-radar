# Silver Layer Schema — USPTO Trademark Case Files (Parquet)

**Status:** Draft v0.1 for review
**Source products:** TRTYRAP (annual applications backfile), TRTDXFAP (daily applications front file)
**Source format:** US Trademark Applications XML, Version 2.0 DTD
**Schema lineage:** Aligned with the USPTO Office of the Chief Economist (OCE)
Trademark Case Files Dataset (Graham, Marco, Miller et al.). Table and column
names follow OCE conventions so that existing research code ports directly.
Authoritative cross-reference: the OCE *Variable Tables* PDF (2023 update) and
*Data Documentation* paper, which map each OCE column to its XML source element.

---

## 1. Design principles

1. **One row per current state.** The dataset reflects each case file's latest
   known state (annual baseline + daily replay, latest-wins on `serial_no`).
   Superseded field values are not retained.
2. **OCE-aligned naming.** Deviate from OCE names only for typing (native
   Parquet DATE/BOOLEAN instead of Stata strings) and for tables OCE does not
   publish (see `correspondent`, an extension).
3. **`serial_no` is the universal key.** 8-character zero-padded **string**
   (never integer — leading zeros are significant). Every child table carries
   it; referential integrity to `case_file` is a build-time assertion.
4. **Child rows are replaced wholesale.** When a daily file touches a serial
   number, all child rows for that serial are deleted and rewritten from the
   new record (a daily record is complete current state, not a delta).
5. **Nothing dropped for size.** All tables ship, including full event history
   and goods/services free text. Consumers filter; we don't pre-filter.

---

## 2. Entity relationships

```
case_file (1 row per serial_no)
 ├── owner              1:M   (serial_no, own_seq, own_type_cd)
 ├── classification     1:M   (serial_no, class_seq)
 │    ├── intl_class    1:M   (serial_no, class_seq, intl_class_cd)
 │    └── us_class      1:M   (serial_no, class_seq, us_class_cd)
 ├── statement          1:M   (serial_no, statement_seq)
 ├── event              1:M   (serial_no, event_seq)
 ├── design_search      1:M   (serial_no, design_search_cd)
 ├── prior_mark         1:M   (serial_no, prior_no)
 ├── foreign_app        1:M   (serial_no, foreign_seq)
 ├── madrid_intl_file   1:M   (serial_no, madrid_seq)
 ├── owner_name_change  1:M   (serial_no, change_seq)
 └── correspondent      1:1   (serial_no)            [extension beyond OCE]
```

---

## 3. Table definitions

Types are Parquet logical types. "XML source" gives the element within each
`<case-file>` record. All XML paths are confirmed (live data + XML
documentation + OCE variable-table mappings, 2026-07-08); the former
**[verify]** markers are resolved in §7.

### 3.1 `case_file` — one row per application/registration

Hub table. Full OCE parity (2023 variable tables) as of 2026-07-08 — every
OCE `case_file.dta` variable has a column here except `tad_file_id` (replaced
by the richer `file_name_source` lineage). `attorney_name` / `atty_dkt_no` /
`domestic_rep_name` live here rather than in a separate correspondent-attorney
table (OCE splits them out).

| Column | Type | XML source | Notes |
|---|---|---|---|
| `serial_no` | STRING | `<serial-number>` | PK. 8 chars, zero-padded. |
| `registration_no` | STRING | `<registration-number>` | Null until registered. |
| `mark_id_char` | STRING | `<case-file-header>/<mark-identification>` | The wordmark text. Primary input to coined-name scoring. |
| `filing_dt` | DATE | `<filing-date>` | |
| `registration_dt` | DATE | `<registration-date>` | |
| `publication_dt` | DATE | `<published-for-opposition-date>` | |
| `abandon_dt` | DATE | `<abandonment-date>` | |
| `cancel_cd` | STRING | `<cancellation-code>` | Section under which the registration was cancelled. |
| `cancel_dt` | DATE | `<cancellation-date>` | Direct element (confirmed live 2026-07-08 and via OCE `reg_cancel_dt`). |
| `cancel_pend_in` | BOOLEAN | `<cancellation-pending-in>` | |
| `renewal_dt` | DATE | `<renewal-date>` | |
| `renewal_file_in` | BOOLEAN | `<renewal-filed-in>` | |
| `status_cd` | STRING | `<status-code>` | Current case status; decode via `lookups/status_codes.csv` (§6). |
| `status_dt` | DATE | `<status-date>` | Date of current status. |
| `mark_draw_cd` | STRING | `<mark-drawing-code>` | Drawing type. **Single raw digit** in v2.0 XML (`4` = standard characters, the primary analysis population) — NOT the 4-digit OCE code. Verified live 2026-07-08. Decode via `lookups/drawing_codes.csv`. |
| `std_char_claimed_in` | BOOLEAN | `<standard-characters-claimed-in>` | |
| `attorney_name` | STRING | `<attorney-name>` | Attorney of record as filed. Central to the filer-clustering analysis. |
| `atty_dkt_no` | STRING | `<attorney-docket-number>` | |
| `domestic_rep_name` | STRING | `<domestic-representative-name>` | |
| `exm_name` | STRING | `<employee-name>` | Examining attorney (element name confirmed live 2026-07-08). |
| `exm_office_cd` | STRING | `<law-office-assigned-location-code>` | |
| `file_location` | STRING | `<current-location>` | |
| `file_location_dt` | DATE | `<location-date>` | |
| `use_af_in` | BOOLEAN | `<use-application-currently-in>` | §1(a) use basis, current. |
| `use_intent_in` | BOOLEAN | `<intent-to-use-current-in>` | §1(b) intent-to-use, current. |
| `for_app_in` | BOOLEAN | `<filing-basis-current-44d-in>` | §44(d), current. |
| `for_reg_in` | BOOLEAN | `<filing-basis-current-44e-in>` | §44(e), current. |
| `intl_reg_in` | BOOLEAN | `<filing-basis-current-66a-in>` | §66(a) Madrid, current. |
| `no_basis_in` | BOOLEAN | `<filing-current-no-basis-in>` | |
| `use_file_in` | BOOLEAN | `<filed-as-use-application-in>` | §1(a) as filed. |
| `use_intent_file_in` | BOOLEAN | `<intent-to-use-in>` | §1(b) as filed. |
| `for_app_file_in` | BOOLEAN | `<filing-basis-filed-as-44d-in>` | §44(d) as filed. |
| `for_reg_file_in` | BOOLEAN | `<filing-basis-filed-as-44e-in>` | §44(e) as filed. |
| `intl_reg_file_in` | BOOLEAN | `<filing-basis-filed-as-66a-in>` | §66(a) as filed. |
| `no_basis_file_in` | BOOLEAN | `<without-basis-currently-in>` | Element name is misleading; OCE maps it to "no basis claim at filing" (`lb_none_file_in`). |
| `amend_reg_dt` | DATE | `<amend-to-register-date>` | |
| `amend_lb_use_in` / `amend_lb_itu_in` / `amend_lb_for_app_in` / `amend_lb_for_reg_in` | BOOLEAN | `<amended-to-{use,itu,44d,44e}-application-in>` | Basis amendments. |
| `amend_principal_in` / `amend_supp_reg_in` | BOOLEAN | `<{principal,supplemental}-register-amended-in>` | Register amendments. |
| `chg_reg_in` | BOOLEAN | `<change-registration-in>` | Any amendment/correction/less goods. |
| `opposition_pend_in` | BOOLEAN | `<opposition-pending-in>` | |
| `interference_pend_in` | BOOLEAN | `<interference-pending-in>` | |
| `concur_use_in` / `concur_use_pend_in` / `concur_use_pub_in` | BOOLEAN | `<concurrent-use-in>` / `<concurrent-use-proceeding-in>` / `<published-concurrent-in>` | Concurrent use. |
| `use_afdv_file_in` / `use_afdv_acc_in` / `use_afdv_par_acc_in` | BOOLEAN | `<section-8-{filed,accepted,partial-accept}-in>` | §8 continued-use affidavits. |
| `incontest_file_in` / `incontest_ack_in` | BOOLEAN | `<section-15-{filed,acknowledged}-in>` | §15 incontestability. |
| `acq_dist_in` / `acq_dist_part_in` | BOOLEAN | `<section-2f-in>` / `<section-2f-in-part-in>` | §2(f) acquired distinctiveness. |
| `repub_12c_in` / `repub_12c_dt` | BOOLEAN / DATE | `<section-12c-in>` / `<republished-12c-date>` | §12(c) republication. |
| `draw_color_file_in` / `draw_color_cur_in` | BOOLEAN | `<color-drawing-{filed,current}-in>` | |
| `draw_3d_file_in` / `draw_3d_cur_in` | BOOLEAN | `<drawing-3d-{filed,current}-in>` | |
| `for_priority_in` | BOOLEAN | `<foreign-priority-in>` | |
| `ir_no` | STRING | `<international-registration>/<international-registration-number>` | Madrid IR number. All `ir_*` columns read the case-file-level `<international-registration>` group (a header sibling, confirmed live 2026-07-08 — these elements never appear in the header). |
| `ir_registration_dt` / `ir_publication_dt` / `ir_renewal_dt` / `ir_death_dt` | DATE | `<international-{registration,publication,renewal,death}-date>` | |
| `ir_status_cd` / `ir_status_dt` | STRING / DATE | `<international-status-{code,date}>` | |
| `ir_auto_reg_dt` | DATE | `<auto-protection-date>` | |
| `ir_first_refus_in` | BOOLEAN | `<first-refusal-in>` | |
| `ir_priority_in` / `ir_priority_dt` | BOOLEAN / DATE | `<priority-claimed-{in,date}>` | |
| `supp_reg_in` | BOOLEAN | `<supplemental-register-in>` | Confirmed live 2026-07-08. |
| `certification_in` / `collective_in` / `coll_serv_mark_in` / `coll_trade_mark_in` / `service_in` / `trade_in` | BOOLEAN | mark-type indicators | v2.0 splits collective into membership (`collective_in`, matching OCE `coll_memb_mark_in`) / service / trademark flags. |
| `related_other_in` | BOOLEAN | `<prior-registration-applications>/<other-related-in>` | "and others" appears in prior-registration list. |
| `file_dt_source` | DATE | *(pipeline)* | Transaction date of the source file that produced this row (lineage; drives latest-wins). |
| `file_name_source` | STRING | *(pipeline)* | Source zip/XML filename. |

Dropped from earlier drafts: `pseudo_mark` — no `<pseudo-mark>` element
exists in v2.0 (all-null live 2026-07-08); pseudo marks are carried as
`PM`-type statements (§3.4), matching OCE, which has no such variable either.

### 3.2 `owner` — parties, 1:M

Mirrors OCE `owner.dta` (19 variables). One row per owner entry per party type.

| Column | Type | XML source | Notes |
|---|---|---|---|
| `serial_no` | STRING | — | FK. |
| `own_seq` | INT32 | `<entry-number>` | Sequence within party type. |
| `own_type_cd` | STRING | `<party-type>` | 10 = original applicant, 20 = owner at publication, 30 = original registrant, 40+ = subsequent owners. PK with serial_no + own_seq. |
| `own_name` | STRING | `<party-name>` | Unnormalized; document as known limitation. |
| `own_altn_name` | STRING | `<dba-aka-text>` | DBA/AKA. |
| `own_entity_cd` | STRING | `<legal-entity-type-code>` | Individual / corporation / LLC etc. |
| `own_entity_desc` | STRING | `<entity-statement>` | |
| `own_composed_of` | STRING | `<composed-of-statement>` | |
| `own_addr_1` | STRING | `<address-1>` | |
| `own_addr_2` | STRING | `<address-2>` | |
| `own_addr_city` | STRING | `<city>` | |
| `own_addr_state_cd` | STRING | `<state>` | |
| `own_addr_postal` | STRING | `<postcode>` | |
| `own_addr_country_cd` | STRING | `<country>` | **Primary geographic-analysis column.** Contains invalid values; document, don't clean. |
| `own_addr_other_cd` | STRING | `<other>` | Non-country region/agency code of the address (OCE `own_addr_other_cd`). |
| `own_nalty_country_cd` | STRING | `<nationality>/<country>` | Citizenship, distinct from address country (confirmed live; OCE name). |
| `own_nalty_state_cd` | STRING | `<nationality>/<state>` | |
| `own_nalty_other_cd` | STRING | `<nationality>/<other>` | |

(An earlier draft carried `own_incorp_state_cd` `<state-incorporation>`; no
such element exists in v2.0 and OCE has no equivalent — removed.)

### 3.3 `classification` — filing classes, 1:M

One row per class block; specific codes hang off it in `intl_class` / `us_class`.

| Column | Type | XML source | Notes |
|---|---|---|---|
| `serial_no` | STRING | — | FK. |
| `class_seq` | INT32 | *(position)* | PK with serial_no. |
| `primary_cd` | STRING | `<primary-code>` | |
| `class_status_cd` | STRING | `<status-code>` | Per-class status (classes can be individually abandoned). |
| `class_status_dt` | DATE | `<status-date>` | |
| `class_intl_count` | INT32 | `<international-code-total-no>` | |
| `class_us_count` | INT32 | `<us-code-total-no>` | |
| `use_first_anywhere_dt` | DATE | `<first-use-anywhere-date>` | Claimed first use — self-reported and unreliable; relevant to non-use analysis. |
| `use_first_commerce_dt` | DATE | `<first-use-in-commerce-date>` | |
| `use_first_anywhere_raw` / `use_first_commerce_raw` | STRING | *(same elements)* | As-entered strings; preserve partial dates (`19870000`) the DATE columns null out. Mirrors OCE's `*_raw` pairs. |

**`intl_class`**: `serial_no`, `class_seq`, `intl_class_cd` (STRING —
`<international-code>`; "009", "021", "025" etc. Zero-padded string, not int).
**`us_class`**: `serial_no`, `class_seq`, `us_class_cd` (STRING — `<us-code>`).

### 3.4 `statement` — typed free text, 1:M

Full inclusion per design decision, including goods/services text.

| Column | Type | XML source | Notes |
|---|---|---|---|
| `serial_no` | STRING | — | FK. |
| `statement_seq` | INT32 | *(position)* | PK with serial_no. |
| `statement_type_cd` | STRING | `<type-code>` | 6-char code. `GS####` = goods/services for a class (last chars encode class). Other families: mark description (`DM`), translation (`TR`), disclaimer (`D1`...), name/portrait consent, lining/stippling. Ship the type-code lookup (§6). |
| `statement_text` | STRING | `<text>` | The largest text column in the dataset. |

Policy: include **all** statement types (simpler pipeline, complete corpus);
the card documents that `GS`-prefixed rows are the goods/services text.

### 3.5 `event` — prosecution history, 1:M

Largest table by rows (~155M+). Mirrors OCE `event.dta`.

| Column | Type | XML source | Notes |
|---|---|---|---|
| `serial_no` | STRING | — | FK. |
| `event_seq` | INT32 | `<number>` | PK with serial_no. |
| `event_cd` | STRING | `<code>` | Event code — cancellations, abandonments, office actions. Key lifecycle signal (post-registration cancellation/abandonment). Ship code lookup (§6). |
| `event_type_cd` | STRING | `<type>` | Event category. |
| `event_dt` | DATE | `<date>` | |

### 3.6 Satellite tables (kept for completeness)

- **`design_search`**: `serial_no`, `design_search_cd` (STRING, `<design-search>/<code>`).
- **`prior_mark`**: `serial_no`, `prior_no` (STRING), `prior_type_cd` (STRING, `<relationship-type>`).
- **`foreign_app`**: `serial_no`, `foreign_seq`, country, application/registration numbers and dates per `<foreign-applications>` block (~13 columns; finalize from OCE variable tables).
- **`madrid_intl_file`**: `serial_no`, `madrid_seq`, IR number/date, status per `<madrid-international-filing-record>` (~11 columns; finalize from OCE variable tables).
- **`owner_name_change`**: `serial_no`, `change_seq`, changed-name text per OCE.

### 3.7 `correspondent` — extension beyond OCE, 1:1

The XML `<correspondent>` block (address lines; line 1 is conventionally the
attorney/firm name). OCE omits this; we include it because filer clustering is
a first-class use case. Columns: `serial_no`, `cor_addr_1` … `cor_addr_5`
(STRING, `<address-1>`…`<address-5>`). Document clearly that these are
unstructured address lines, not parsed name fields.

---

## 4. Update semantics (normative summary)

1. **Baseline:** rebuild all silver tables from the latest annual (TRTYRAP)
   into a candidate build directory.
2. **Replay:** apply dailies (TRTDXFAP) with transaction date after the
   annual's coverage cutoff (Dec 31 of covered year), in date order. Per
   serial number: upsert `case_file` row; delete-and-rewrite all child rows.
3. **Reconcile** (§5), then atomically swap candidate → live.
4. **Prune** dailies at or before the cutoff only after the swap.
5. Dataset carries a single `data_current_through` date = transaction date of
   the last daily applied.

## 5. Build-time assertions (publish gates)

- `case_file.serial_no` unique; all child `serial_no` values exist in `case_file`.
- Per-filing-year counts within tolerance of USPTO published statistics;
  total count reconciles against the OCE-published figure for the
  overlapping period (12.7M through March 2024) modulo cutoff delta.
- Date policy (calibrated against the real corpus, 2026-07): floor
  1870-01-01 for all date columns; ceiling is build date + 120 days for
  scheduled-action columns (`publication_dt`, `registration_dt` — the
  Official Gazette runs weeks ahead), build date + 40 years for
  expiration/renewal columns, build date otherwise. The gate fails only if
  a column's out-of-policy count exceeds 0.1% of its table (systematic
  parse-bug territory); smaller counts are source dirt, published in
  `profile/date_outliers.csv` rather than cleaned.
- Null-rate profile emitted per column and published alongside the data.
- Every row's `file_name_source` exists in the job ledger's download records.

## 6. Repo layout (Hugging Face)

```
data/
  case_file/         part-*.parquet   (sorted by serial_no)
  owner/             part-*.parquet
  classification/    intl_class/  us_class/
  statement/         part-*.parquet   (largest by bytes)
  event/             part-*.parquet   (largest by rows)
  design_search/  prior_mark/  foreign_app/  madrid_intl_file/
  owner_name_change/  correspondent/
lookups/
  status_codes.csv  event_codes.csv  statement_types.csv
  party_types.csv   entity_codes.csv  drawing_codes.csv
profile/
  null_rates.csv    row_counts.csv   build_lineage.json
README.md            (dataset card)
```

Target part-file size 256–512 MB; sort within files by `serial_no` so
row-group min/max statistics make serial-range lookups cheap. Date-range
consumers (post-2015 cuts) benefit from `filing_dt` row-group stats in
`case_file`; no hive partitioning needed at this scale.

## 7. Open items — all resolved

1. ~~Confirm every **[verify]** XML path~~ **Done 2026-07-08:** confirmed
   against live data (2000-case inventory), the XML documentation, and the
   OCE 2023 variable tables' element mappings. Outcomes: `exm_name` =
   `<employee-name>`; `cancel_dt` is a direct element; `own_incorp_state_cd`
   and `pseudo_mark` removed (no such v2.0 elements); madrid status =
   `<international-status-code>`; `<name-change-explanation>` repeats per
   owner.
2. ~~Diff `case_file` against the OCE 2023 variable tables~~ **Done
   2026-07-08:** full OCE parity (§3.1); also added OCE's classification
   count/raw-date columns and the owner nationality/other-code columns.
3. ~~Confirm `pseudo_mark` cardinality~~ **Done:** not a field at all in
   v2.0 — pseudo marks are `PM`-type statements (§3.4).
4. ~~DTD version ≠ 2.0 handling~~ **Done:** hard-stop, implemented in
   `parse_xml` (`DtdVersionError`).
5. ~~Source the lookup CSVs (status/event/statement codes)~~ **Done
   2026-07-08:** transcribed from the TRTDXFAP product documentation and the
   OCE Trademark Case Files Dataset event CSV into
   `uspto_trademark_radar/lookups/`, provenance pinned per table in
   `lookups/_sources.json` (see docs/DATA_SOURCES.md "Code lookup tables").
