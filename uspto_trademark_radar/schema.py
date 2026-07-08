"""PyArrow schemas for the silver layer — normative source: docs/silver-schema.md.

Every table carries the two lineage columns (`file_dt_source`,
`file_name_source`); on child tables they additionally drive the latest-wins
merge (a child row survives only if it came from the same source file as the
winning `case_file` row for its serial).

`serial_no` is an 8-char zero-padded STRING everywhere. Class codes are
zero-padded strings, never ints.
"""

from __future__ import annotations

import pyarrow as pa

_LINEAGE = [
    ("file_dt_source", pa.date32()),
    ("file_name_source", pa.string()),
]


def _schema(*cols: tuple[str, pa.DataType]) -> pa.Schema:
    return pa.schema(list(cols) + _LINEAGE)


CASE_FILE = _schema(
    ("serial_no", pa.string()),
    ("registration_no", pa.string()),
    ("mark_id_char", pa.string()),
    ("filing_dt", pa.date32()),
    ("registration_dt", pa.date32()),
    ("publication_dt", pa.date32()),
    ("abandon_dt", pa.date32()),
    ("cancel_cd", pa.string()),
    ("cancel_dt", pa.date32()),
    ("cancel_pend_in", pa.bool_()),
    ("renewal_dt", pa.date32()),
    ("renewal_file_in", pa.bool_()),
    ("status_cd", pa.string()),
    ("status_dt", pa.date32()),
    ("mark_draw_cd", pa.string()),
    ("std_char_claimed_in", pa.bool_()),
    ("attorney_name", pa.string()),
    ("atty_dkt_no", pa.string()),
    ("domestic_rep_name", pa.string()),
    ("exm_name", pa.string()),
    ("exm_office_cd", pa.string()),
    ("file_location", pa.string()),
    ("file_location_dt", pa.date32()),
    # filing basis, current state and as-filed
    ("use_af_in", pa.bool_()),
    ("use_intent_in", pa.bool_()),
    ("for_app_in", pa.bool_()),
    ("for_reg_in", pa.bool_()),
    ("intl_reg_in", pa.bool_()),
    ("no_basis_in", pa.bool_()),
    ("use_file_in", pa.bool_()),
    ("use_intent_file_in", pa.bool_()),
    ("for_app_file_in", pa.bool_()),
    ("for_reg_file_in", pa.bool_()),
    ("intl_reg_file_in", pa.bool_()),
    ("no_basis_file_in", pa.bool_()),
    # amendments
    ("amend_reg_dt", pa.date32()),
    ("amend_lb_use_in", pa.bool_()),
    ("amend_lb_itu_in", pa.bool_()),
    ("amend_lb_for_app_in", pa.bool_()),
    ("amend_lb_for_reg_in", pa.bool_()),
    ("amend_principal_in", pa.bool_()),
    ("amend_supp_reg_in", pa.bool_()),
    ("chg_reg_in", pa.bool_()),
    # proceedings pending
    ("opposition_pend_in", pa.bool_()),
    ("interference_pend_in", pa.bool_()),
    # concurrent use
    ("concur_use_in", pa.bool_()),
    ("concur_use_pend_in", pa.bool_()),
    ("concur_use_pub_in", pa.bool_()),
    # post-registration maintenance: sections 8 / 15 / 2(f) / 12(c)
    ("use_afdv_file_in", pa.bool_()),
    ("use_afdv_acc_in", pa.bool_()),
    ("use_afdv_par_acc_in", pa.bool_()),
    ("incontest_file_in", pa.bool_()),
    ("incontest_ack_in", pa.bool_()),
    ("acq_dist_in", pa.bool_()),
    ("acq_dist_part_in", pa.bool_()),
    ("repub_12c_in", pa.bool_()),
    ("repub_12c_dt", pa.date32()),
    # drawing characteristics
    ("draw_color_file_in", pa.bool_()),
    ("draw_color_cur_in", pa.bool_()),
    ("draw_3d_file_in", pa.bool_()),
    ("draw_3d_cur_in", pa.bool_()),
    # foreign priority + header-level Madrid IR state
    ("for_priority_in", pa.bool_()),
    ("ir_no", pa.string()),
    ("ir_registration_dt", pa.date32()),
    ("ir_publication_dt", pa.date32()),
    ("ir_renewal_dt", pa.date32()),
    ("ir_death_dt", pa.date32()),
    ("ir_status_cd", pa.string()),
    ("ir_status_dt", pa.date32()),
    ("ir_auto_reg_dt", pa.date32()),
    ("ir_first_refus_in", pa.bool_()),
    ("ir_priority_in", pa.bool_()),
    ("ir_priority_dt", pa.date32()),
    # register + mark-type flags
    ("supp_reg_in", pa.bool_()),
    ("certification_in", pa.bool_()),
    ("collective_in", pa.bool_()),
    ("coll_serv_mark_in", pa.bool_()),
    ("coll_trade_mark_in", pa.bool_()),
    ("service_in", pa.bool_()),
    ("trade_in", pa.bool_()),
    ("related_other_in", pa.bool_()),
)

OWNER = _schema(
    ("serial_no", pa.string()),
    ("own_seq", pa.int32()),
    ("own_type_cd", pa.string()),
    ("own_name", pa.string()),
    ("own_altn_name", pa.string()),
    ("own_entity_cd", pa.string()),
    ("own_entity_desc", pa.string()),
    ("own_composed_of", pa.string()),
    ("own_addr_1", pa.string()),
    ("own_addr_2", pa.string()),
    ("own_addr_city", pa.string()),
    ("own_addr_state_cd", pa.string()),
    ("own_addr_postal", pa.string()),
    ("own_addr_country_cd", pa.string()),
    ("own_addr_other_cd", pa.string()),
    ("own_nalty_country_cd", pa.string()),
    ("own_nalty_state_cd", pa.string()),
    ("own_nalty_other_cd", pa.string()),
)

CLASSIFICATION = _schema(
    ("serial_no", pa.string()),
    ("class_seq", pa.int32()),
    ("primary_cd", pa.string()),
    ("class_status_cd", pa.string()),
    ("class_status_dt", pa.date32()),
    ("class_intl_count", pa.int32()),
    ("class_us_count", pa.int32()),
    ("use_first_anywhere_dt", pa.date32()),
    ("use_first_commerce_dt", pa.date32()),
    # as-entered date strings: the source contains partial dates (e.g.
    # 19870000, year-only first-use claims) that the DATE columns null out
    ("use_first_anywhere_raw", pa.string()),
    ("use_first_commerce_raw", pa.string()),
)

INTL_CLASS = _schema(
    ("serial_no", pa.string()),
    ("class_seq", pa.int32()),
    ("intl_class_cd", pa.string()),
)

US_CLASS = _schema(
    ("serial_no", pa.string()),
    ("class_seq", pa.int32()),
    ("us_class_cd", pa.string()),
)

STATEMENT = _schema(
    ("serial_no", pa.string()),
    ("statement_seq", pa.int32()),
    ("statement_type_cd", pa.string()),
    ("statement_text", pa.string()),
)

EVENT = _schema(
    ("serial_no", pa.string()),
    ("event_seq", pa.int32()),
    ("event_cd", pa.string()),
    ("event_type_cd", pa.string()),
    ("event_dt", pa.date32()),
)

DESIGN_SEARCH = _schema(
    ("serial_no", pa.string()),
    ("design_search_cd", pa.string()),
)

PRIOR_MARK = _schema(
    ("serial_no", pa.string()),
    ("prior_no", pa.string()),
    ("prior_type_cd", pa.string()),
)

FOREIGN_APP = _schema(
    ("serial_no", pa.string()),
    ("foreign_seq", pa.int32()),
    ("for_country_cd", pa.string()),
    ("for_other", pa.string()),
    ("for_app_no", pa.string()),
    ("for_app_dt", pa.date32()),
    ("for_reg_no", pa.string()),
    ("for_reg_dt", pa.date32()),
    ("for_reg_exp_dt", pa.date32()),
    ("for_renewal_no", pa.string()),
    ("for_renewal_dt", pa.date32()),
    ("for_renewal_exp_dt", pa.date32()),
    ("for_priority_claim_in", pa.bool_()),
)

MADRID_INTL_FILE = _schema(
    ("serial_no", pa.string()),
    ("madrid_seq", pa.int32()),
    ("madrid_ref_no", pa.string()),
    ("madrid_orig_filing_dt", pa.date32()),
    ("ir_no", pa.string()),
    ("ir_dt", pa.date32()),
    ("madrid_status_cd", pa.string()),
    ("madrid_status_dt", pa.date32()),
    ("irregularity_reply_by_dt", pa.date32()),
    ("ir_renewal_dt", pa.date32()),
)

OWNER_NAME_CHANGE = _schema(
    ("serial_no", pa.string()),
    ("change_seq", pa.int32()),
    ("change_text", pa.string()),
)

CORRESPONDENT = _schema(
    ("serial_no", pa.string()),
    ("cor_addr_1", pa.string()),
    ("cor_addr_2", pa.string()),
    ("cor_addr_3", pa.string()),
    ("cor_addr_4", pa.string()),
    ("cor_addr_5", pa.string()),
)

# Every silver table, in schema-doc order. Keys are the on-disk directory
# names under data/ in the published repo (schema §6).
TABLES: dict[str, pa.Schema] = {
    "case_file": CASE_FILE,
    "owner": OWNER,
    "classification": CLASSIFICATION,
    "intl_class": INTL_CLASS,
    "us_class": US_CLASS,
    "statement": STATEMENT,
    "event": EVENT,
    "design_search": DESIGN_SEARCH,
    "prior_mark": PRIOR_MARK,
    "foreign_app": FOREIGN_APP,
    "madrid_intl_file": MADRID_INTL_FILE,
    "owner_name_change": OWNER_NAME_CHANGE,
    "correspondent": CORRESPONDENT,
}
