"""Grouping-validation harness: keying variants, deterministic sampling,
and the audit/sensitivity artifacts. All names/addresses are fictional."""
import json
from pathlib import Path

import pytest

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.gold import mark_features, resolution_audit
from uspto_trademark_radar.gold.coined_scorer import CoinedScorer
from uspto_trademark_radar.gold.entity_resolution import operation_keys
from uspto_trademark_radar.integrity import sha256_file

# Ordinary-English reference for the coined scorer; stands in for the
# production pre-2015 baseline, which a tiny fixture corpus cannot provide.
_REFERENCE = """
apple orange banana table chair window garden flower morning evening
water river mountain forest bridge castle silver golden simple gentle
market bright shadow thunder harvest wonder journey lantern harbor
premium natural organic classic modern comfort quality wedding titan
""".split() * 20

ROWS = [
    ("1", "ADDR1", "DANA RIVERS"),
    ("2", "ADDR2", "DANA RIVERS"),
    ("3", "ADDR3", None),
    ("4", None, "KIM LAKE"),
]


def test_name_only_ignores_addresses():
    keys = resolution_audit.variant_keys(ROWS, "name_only")
    assert keys == {"1": "N:DANA RIVERS", "2": "N:DANA RIVERS",
                    "4": "N:KIM LAKE"}


def test_addr_only_ignores_names():
    keys = resolution_audit.variant_keys(ROWS, "addr_only")
    assert keys == {"1": "A:ADDR1", "2": "A:ADDR2", "3": "A:ADDR3"}


def test_addr_first_prefers_the_address_signal():
    keys = resolution_audit.variant_keys(ROWS, "addr_first")
    assert keys["1"] == "A:ADDR1"          # address wins when present
    assert keys["4"] == "N:KIM LAKE"       # name is still the fallback


def test_shipped_variant_is_exactly_entity_resolution():
    assert resolution_audit.variant_keys(ROWS, "shipped") == operation_keys(ROWS)


def test_hub_caps_move_the_name_hub_cutoff():
    # one name across 30 addresses: a hub under the strict cap (25),
    # an ordinary name under the loose cap (100).
    rows = [(str(i), f"ADDR{i}", "COMMON NAME") for i in range(30)]
    strict = resolution_audit.variant_keys(rows, "strict_hubs")
    assert all(k.startswith("A:") for k in strict.values())
    loose = resolution_audit.variant_keys(rows, "loose_hubs")
    assert all(k == "N:COMMON NAME" for k in loose.values())


def test_sampling_is_deterministic_stratified_and_dedup():
    sizes = {f"N:OP{i:04d}": 20 + i for i in range(400)}   # sizes 20..419
    a = resolution_audit.sample_operations(sizes)
    b = resolution_audit.sample_operations(sizes)
    assert a == b                                   # content hash, no RNG
    assert len(a) == len(set(a))                    # no duplicates
    largest = sorted(sizes, key=lambda k: (-sizes[k], k))
    assert set(largest[:resolution_audit.FORCED_LARGEST]) <= set(a)
    quota = sum(q for _, _, q in resolution_audit.SAMPLE_STRATA)
    assert len(a) <= resolution_audit.FORCED_LARGEST + quota


def _rotating_address_case(serial, mark, owner):
    """High-churn archetype with a DIFFERENT correspondent address on each
    filing (the fragmentation evasion) and one attorney of record."""
    cf = make_case_file(serial, mark=mark, owner_name=owner,
                        owner_country="CN",
                        statement="; ".join(f"Item {i}" for i in range(15)))
    return cf.replace("<address-1>ACME IP LLC</address-1>",
                      f"<address-1>Suite {serial}</address-1>"
                      f"<address-2>{serial} Rotating Blvd</address-2>"
                      f"<address-3>Anytown, NV 89000</address-3>")


def _build(cfg, ledger, case_files, name="apc18840407-20241231-01.zip"):
    z = make_zip(cfg.bronze_annual / name, make_xml(case_files))
    ledger.record_download(name, "TRTYRAP", "http://t", z.stat().st_size,
                           sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


def test_validation_artifacts_end_to_end(cfg, ledger):
    # 25 filings, one attorney, 25 rotating addresses: shipped/name keying
    # holds them together; address-only keying fragments them all below
    # the profiling threshold.
    cases = [_rotating_address_case(f"{7200000+i}", f"VOFUL{i:02d}",
                                    f"Example Holdings {i} Co")
             for i in range(25)]
    candidate = _build(cfg, ledger, cases)
    gold_root = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(_REFERENCE)
    mark_features.build(candidate, gold_root, scorer=scorer,
                        log=lambda *a: None)

    out = resolution_audit.build_audit_sample(candidate, gold_root,
                                              log=lambda *a: None)
    doc = json.loads((out / "audit_sample.json").read_text(encoding="utf-8"))
    assert doc["review_guide"]
    assert len(doc["operations"]) == 1
    op = doc["operations"][0]
    assert op["key_type"] == "name"
    assert op["n_marks"] == 25
    assert op["n_addresses"] == 25
    assert op["n_applicants"] == 25
    assert op["attorney_names"][0]["n"] == 25       # one spelling, 25 uses
    assert len(op["addresses"]) == 15               # capped evidence list

    report = resolution_audit.sensitivity(candidate, gold_root,
                                          log=lambda *a: None)
    v = report["variants"]
    assert set(v) == set(resolution_audit.VARIANTS)
    assert v["shipped"]["n_operations"] == 1
    assert v["shipped"]["filings_profiled"] == 25
    assert v["name_only"]["n_operations"] == 1
    # address rotation defeats any address-preferring rule
    assert v["addr_only"]["n_operations"] == 0
    assert v["addr_first"]["n_operations"] == 0
    assert v["name_only"]["vs_shipped"]["shared_filings"] == 25
    # constant scores on the tiny fixture leave spearman undefined; the
    # top-decile sets still align exactly
    assert v["name_only"]["vs_shipped"]["top_decile_filing_jaccard"] == 1.0

    # the review app renders with the evidence inlined and no leftover
    # sentinel, and it is written alongside the sample
    html = (out / resolution_audit.REVIEW_APP).read_text(encoding="utf-8")
    assert "/*__PAYLOAD__*/" not in html
    assert "Grouping precision audit" in html
    assert '"n_marks": 25' in html            # the sampled op's evidence
    assert (gold_root / "resolution_sensitivity.json").exists()


def _doc_with_evidence(value: str) -> dict:
    return {"silver_build": "b", "version": "1", "review_guide": "g",
            "operations": [{
                "op_id": 1, "key_type": "name", "op_key": "N:X", "n_marks": 1,
                "first_year": 2015, "last_year": 2016, "n_name_spellings": 1,
                "n_addresses": 1, "n_applicants": 1,
                "attorney_names": [{"value": value, "n": 1}],
                "addresses": [], "applicants": [], "owner_countries": []}]}


def test_review_app_escapes_script_and_line_separators():
    # USPTO free-text could contain a literal </script> or U+2028; either
    # would break the inlined-payload app if not escaped.
    ls = chr(0x2028)
    html = resolution_audit.render_review_app(
        _doc_with_evidence("EVIL</script><script>BAD" + ls + "Z"))
    assert "</script><script>BAD" not in html      # not injected verbatim
    assert chr(92) + "u003c/script" in html         # escaped to <
    assert ls not in html                            # raw separator gone
    assert chr(92) + "u2028" in html
    assert html.count("</script>") == 1             # only the app's own tag


def test_downloaded_verdicts_carry_no_free_text():
    # the committed artifact must be name-free by construction: the export
    # builder in the app emits only structured fields, never notes.
    html = resolution_audit.render_review_app(_doc_with_evidence("X"))
    dl = html[html.index("function download()"):html.index("function importFile")]
    assert "notes:" not in dl                        # notes never exported
    assert "verdict:" in dl and "op_id:" in dl


def test_load_base_rejects_build_mismatch(tmp_path: Path):
    import duckdb
    from uspto_trademark_radar.gold import mark_features
    gold_root = tmp_path / "gold"
    mf = gold_root / mark_features.TABLE
    mf.mkdir(parents=True)
    (mf / "_lineage.json").write_text(
        json.dumps({"silver_build": "build-A"}), encoding="utf-8")
    con = duckdb.connect()
    try:
        with pytest.raises(SystemExit, match="rebuild the gold tables"):
            resolution_audit.load_base(con, tmp_path / "build-B", gold_root)
    finally:
        con.close()


def test_wilson_edges_and_symmetry():
    assert resolution_audit.wilson(0, 0) == (0.0, 0.0)
    lo, hi = resolution_audit.wilson(5, 5)    # p=1: upper near 1, lower below
    assert hi == 1.0 and 0.5 < lo < 1.0
    lo0, hi0 = resolution_audit.wilson(0, 5)  # p=0: lower 0, upper above
    assert lo0 == 0.0 and 0.0 < hi0 < 0.5
    a = resolution_audit.wilson(2, 10)        # Wilson is symmetric about 0.5
    b = resolution_audit.wilson(8, 10)
    assert abs(a[0] - (1 - b[1])) < 1e-9
    assert abs(a[1] - (1 - b[0])) < 1e-9


def _write_sample(gold_root: Path, build: str, ops: list[tuple[int, int]]):
    """Minimal audit_sample.json with (op_id, n_marks) rows for scoring."""
    d = gold_root / resolution_audit.AUDIT_TABLE
    d.mkdir(parents=True, exist_ok=True)
    (d / "audit_sample.json").write_text(json.dumps({
        "silver_build": build,
        "operations": [{"op_id": oid, "n_marks": n} for oid, n in ops],
    }), encoding="utf-8")


def test_score_verdicts_precision_and_strata(tmp_path: Path):
    gold_root = tmp_path / "gold"
    # three strata: 30 (20-99), 150 (100-999), 1500 (1000+)
    _write_sample(gold_root, "buildX",
                  [(1, 30), (2, 30), (3, 150), (4, 150), (5, 1500)])
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"silver_build": "buildX", "verdicts": [
        {"op_id": 1, "verdict": "coherent"},
        {"op_id": 2, "verdict": "mixed"},
        {"op_id": 3, "verdict": "coherent"},
        {"op_id": 4, "verdict": "coherent"},
        {"op_id": 5, "verdict": "unclear"},   # excluded from denominator
    ]}), encoding="utf-8")

    rep = resolution_audit.score_verdicts(gold_root, verdicts,
                                          log=lambda *a: None)
    assert rep["counts"] == {"coherent": 3, "mixed": 1, "unclear": 1,
                             "unreviewed": 0, "invalid": 0, "stale": 0}
    assert rep["overall"]["precision"] == 0.75      # 3 / (3+1)
    assert rep["overall"]["adjudicated"] == 4
    assert rep["by_stratum"]["20-99"]["precision"] == 0.5    # 1 coherent, 1 mixed
    assert rep["by_stratum"]["100-999"]["precision"] == 1.0  # both coherent
    assert "1000+" not in rep["by_stratum"]         # only the unclear op there
    assert not rep["warnings"]
    lo, hi = rep["overall"]["wilson95"]
    assert 0.0 < lo < 0.75 < hi < 1.0
    assert (verdicts.parent / "resolution_precision.json").exists()


def _colo_case(serial, mark, owner, attorney, street="500 SHARED PLAZA"):
    """A peculiar filing (coined mark, CN owner, marketplace class, long
    goods list) by a given attorney at a shared correspondent street.
    All names/addresses are fictional."""
    cf = make_case_file(serial, mark=mark, owner_name=owner, owner_country="CN",
                        statement="; ".join(f"Item {i}" for i in range(15)))
    cf = cf.replace("<attorney-name>Jane Q. Filer</attorney-name>",
                    f"<attorney-name>{attorney}</attorney-name>")
    return cf.replace("<address-2>1 Main St</address-2>",
                      f"<address-2>{street}</address-2>")


def test_colocation_flags_shared_address_without_merging(cfg, ledger):
    # two distinct attorneys, one shared correspondent street, peculiar
    # portfolios each: the diagnostic should surface the address with both
    # names - but the grouping keeps them as separate name-operations.
    cases = []
    for i in range(8):
        cases.append(_colo_case(f"{7300000+i}", f"VOFUL{i:02d}",
                                f"Alpha Holdings {i} Co", "Alex Stone"))
    for i in range(8):
        cases.append(_colo_case(f"{7400000+i}", f"ZORVE{i:02d}",
                                f"Beta Holdings {i} Co", "Blair Reed"))
    candidate = _build(cfg, ledger, cases)
    gold_root = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(_REFERENCE)
    mark_features.build(candidate, gold_root, scorer=scorer, log=lambda *a: None)

    rep = resolution_audit.colocation(candidate, gold_root, log=lambda *a: None)
    assert rep["qualifying_addresses"] >= 1
    assert rep["note"] and "asserts no" in rep["note"].lower()
    assert "local-only" in rep["scope"]
    shared = [a for a in rep["addresses"] if a["n_distinct_names"] == 2]
    assert shared, "the shared address should host two distinct names"
    names = {n["name"] for n in shared[0]["names"]}
    assert names == {"ALEX STONE", "BLAIR REED"}
    assert (gold_root / "resolution_colocation.json").exists()

    # the grouping itself still keeps them apart (no merge): sharing an
    # address does not fuse two distinct non-hub names
    import duckdb
    con = duckdb.connect()
    try:
        resolution_audit.load_base(con, candidate, gold_root)
        comp = resolution_audit.variant_keys(
            resolution_audit.signal_rows(con), "shipped")
    finally:
        con.close()
    assert len(set(comp.values())) == 2   # two name-operations, not one


def test_score_verdicts_warns_on_build_mismatch(tmp_path: Path):
    gold_root = tmp_path / "gold"
    _write_sample(gold_root, "current_build", [(1, 30)])
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"silver_build": "stale_build", "verdicts": [
        {"op_id": 1, "verdict": "coherent", "n_marks": 999},   # size drift too
    ]}), encoding="utf-8")
    rep = resolution_audit.score_verdicts(gold_root, verdicts,
                                          log=lambda *a: None)
    assert any("op ids may not line up" in w for w in rep["warnings"])
    assert any("n_marks" in w for w in rep["warnings"])
