"""operation_profile builds, scores, and flags shared-service operations."""
import json

import duckdb

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.gold import operation_profile
from uspto_trademark_radar.gold.coined_scorer import CoinedScorer
from uspto_trademark_radar.integrity import sha256_file

# Ordinary-English reference for the coined scorer; stands in for the
# production pre-2015 baseline, which a tiny fixture corpus cannot provide.
_REFERENCE = """
apple orange banana table chair window garden flower morning evening
water river mountain forest bridge castle silver golden simple gentle
market bright shadow thunder harvest wonder journey lantern harbor
premium natural organic classic modern comfort quality wedding titan
""".split() * 20


def _high_churn_case(serial, mark, owner, addr_street):
    """Fixture matching the high-churn archetype: coined mark, CN owner,
    marketplace class, use-basis, long goods list, unique disposable owner,
    shared correspondent address. All values are fictional test data."""
    cf = make_case_file(serial, mark=mark, owner_name=owner, owner_country="CN",
                        statement="; ".join(f"Item {i}" for i in range(15)))
    # inject a correspondent street shared across the operation
    return cf.replace("<address-1>ACME IP LLC</address-1>",
                      f"<address-1>Agent {serial}</address-1>"
                      f"<address-2>{addr_street}</address-2>"
                      f"<address-3>Exampleville, CA 90000</address-3>")


def _rotating_address_case(serial, mark, owner):
    """Same archetype but each filing uses a DIFFERENT correspondent address
    (the fragmentation evasion) while keeping one attorney of record."""
    cf = make_case_file(serial, mark=mark, owner_name=owner, owner_country="CN",
                        statement="; ".join(f"Item {i}" for i in range(15)))
    return cf.replace("<address-1>ACME IP LLC</address-1>",
                      f"<address-1>Suite {serial}</address-1>"
                      f"<address-2>{serial} Rotating Blvd</address-2>"
                      f"<address-3>Anytown, NV 89000</address-3>")


def _build(cfg, ledger, case_files, name="apc18840407-20241231-01.zip"):
    z = make_zip(cfg.bronze_annual / name, make_xml(case_files))
    ledger.record_download(name, "TRTYRAP", "http://t", z.stat().st_size, sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


def test_operation_profile_scores_archetype_high(cfg, ledger):
    # 25 archetype marks, all from one correspondent street, distinct owners
    cases = [_high_churn_case(f"{7000000+i}", f"VOFUL{i:02d}",
                              f"Example Holdings {i} Co", "1 EXAMPLE ROAD")
             for i in range(25)]
    candidate = _build(cfg, ledger, cases)

    gold_root = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(_REFERENCE)
    out = operation_profile.build(candidate, gold_root, scorer=scorer,
                                  log=lambda *a: None)
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT op_key, n_marks, pct_cn, coined_rate, owner_turnover, "
        f"peculiarity_score, is_shared_service "
        f"FROM read_parquet('{out.as_posix()}/*.parquet')"
    ).fetchall()
    assert len(rows) == 1                       # one operation (shared street)
    op = rows[0]
    assert op[1] == 25                          # all marks on the operation
    assert op[2] == 1.0                         # 100% CN
    assert op[3] == 1.0                         # 100% coined
    assert op[4] == 1.0                         # every mark a fresh owner
    assert op[5] > 0.7                          # high peculiarity score
    assert op[6] is False                       # not a shared-service address

    assert op[0] == "N:FILER JANE"               # keyed on the shared attorney

    meta = json.loads((out / "_lineage.json").read_text())
    assert meta["version"] == "1" and meta["n_operations"] == 1


def test_grouping_resists_address_rotation(cfg, ledger):
    # 25 marks, one attorney of record, a DIFFERENT correspondent address on
    # each. Address-keyed grouping would fragment into 25 sub-threshold groups
    # (all dropped); connected components link them by the shared name.
    cases = [_rotating_address_case(f"{7100000+i}", f"VOFUL{i:02d}",
                                    f"Example Holdings {i} Co")
             for i in range(25)]
    candidate = _build(cfg, ledger, cases)
    scorer = CoinedScorer(order=3).fit(_REFERENCE)
    out = operation_profile.build(candidate, cfg.data_root / "gold",
                                  scorer=scorer, log=lambda *a: None)
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT op_key, n_marks, n_addresses FROM "
        f"read_parquet('{out.as_posix()}/*.parquet')"
    ).fetchall()
    assert len(rows) == 1                        # not fragmented
    assert rows[0][1] == 25                       # all marks consolidated
    assert rows[0][2] == 25                       # despite 25 distinct addresses
    # keyed on the shared (non-hub) attorney name, not any single address
    assert rows[0][0] == "N:FILER JANE"


def test_gold_never_in_publish_allowlist():
    # guard: the publish stage must not upload gold outputs
    from uspto_trademark_radar import publish
    src = publish.publish.__code__.co_consts
    assert not any("gold" in str(c) for c in src if isinstance(c, (str, tuple)))
