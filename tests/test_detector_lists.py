"""Detector lists: an adjudicated blocklist + a screened candidate list,
both keyed on a normalized mark string, keyed on the filing not a person."""
import json

from conftest import make_case_file, make_xml, make_zip
from uspto_trademark_radar import parse_stage
from uspto_trademark_radar.build import build_candidate
from uspto_trademark_radar.gold import detector_lists, mark_features, operation_profile
from uspto_trademark_radar.gold.coined_scorer import CoinedScorer
from uspto_trademark_radar.integrity import sha256_file

_EV = ('<case-file-event-statement><code>{code}</code><type>O</type>'
       '<date>20220301</date><number>9</number></case-file-event-statement>')


def _build(cfg, ledger, case_files, name="apc18840407-20241231-01.zip"):
    z = make_zip(cfg.bronze_annual / name, make_xml(case_files))
    ledger.record_download(name, "TRTYRAP", "http://t", z.stat().st_size,
                           sha256_file(z))
    parse_stage.plan(cfg, ledger)
    parse_stage.work(cfg, ledger, log=lambda *a: None)
    cutoff, zips = parse_stage.input_zips(cfg)
    return build_candidate(cfg, cutoff, zips)


def _lines(path):
    return [ln for ln in path.read_text().splitlines() if ln]


def test_tier1_is_the_adjudicated_marks_keyed_and_normalized(cfg, ledger):
    cases = [
        # sanctioned via event; punctuation/case must normalize away
        make_case_file("60000001", mark="Zorvex Gear!",
                       extra_events=_EV.format(code="KBOC")),
        # sanctioned via status 610
        make_case_file("60000002", mark="QVOWII").replace(
            "<status-code>630</status-code>", "<status-code>610</status-code>"),
        # not sanctioned -> must NOT appear on the blocklist
        make_case_file("60000003", mark="MORNING HARBOR"),
    ]
    candidate = _build(cfg, ledger, cases)
    gold = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(["silver", "river", "garden"] * 40)
    mark_features.build(candidate, gold, scorer=scorer, log=lambda *a: None)
    feat = gold / mark_features.TABLE

    out = detector_lists.build(candidate, feat, gold, log=lambda *a: None)

    tier1 = set(_lines(out / "tier1.txt"))
    assert tier1 == {"ZORVEXGEAR", "QVOWII"}   # normalized; survivor excluded

    manifest = json.loads((out / "_manifest.json").read_text())
    assert manifest["tier1"]["n_marks"] == 2
    assert manifest["key_rule"] == detector_lists.KEY_RULE


def test_tier2_threshold_gates_the_candidate_list(cfg, ledger):
    # A single-word mark and a two-word mark, both marketplace-class; the
    # single-token weight puts the one-word mark above a threshold the two-word
    # mark falls below, so the threshold gates membership.
    cases = [
        make_case_file("60000010", mark="ZORVEX"),          # single token
        make_case_file("60000011", mark="MORNING HARBOR"),  # two tokens
    ]
    candidate = _build(cfg, ledger, cases)
    gold = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(["morning", "harbor", "silver"] * 40)
    mark_features.build(candidate, gold, scorer=scorer, log=lambda *a: None)
    feat = gold / mark_features.TABLE

    out = detector_lists.build(candidate, feat, gold, tier2_threshold=1.6,
                               log=lambda *a: None)
    tier2 = set(_lines(out / "tier2.txt"))
    assert "ZORVEX" in tier2            # 1.0 coined + 0.5 mkt + 0.2 single_token
    assert "MORNINGHARBOR" not in tier2  # no single-token weight -> below 1.6


def test_tier3_flags_large_operation_membership_only(cfg, ledger):
    # One operation of 22 distinct-owner filings (a large, high-churn cluster)
    # under a shared attorney, plus a 3-filing operation under a different
    # attorney. Tier 3 flags the large one's marks and excludes the small one.
    cases = [make_case_file(f"7000{i:04d}", mark=f"ZORVIX{i:03d}",
                            owner_name=f"Bulk Owner {i} Co") for i in range(22)]
    cases += [make_case_file(f"7100{i:04d}", mark=f"SMALLMK{i}",
                             owner_name=f"Tiny Owner {i}").replace(
                                 "Jane Q. Filer", "Solo Practitioner")
              for i in range(3)]
    candidate = _build(cfg, ledger, cases)
    gold = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(["zorvix", "bulk", "owner"] * 40)
    mark_features.build(candidate, gold, scorer=scorer, log=lambda *a: None)
    operation_profile.build(candidate, gold, scorer=scorer, log=lambda *a: None)

    out = detector_lists.build(
        candidate, gold / mark_features.TABLE, gold,
        profile_dir=gold / operation_profile.TABLE,
        membership_dir=gold / operation_profile.MEMBERSHIP_TABLE,
        tier3_min_size=20, tier3_min_churn=0.6, log=lambda *a: None)

    tier3 = set(_lines(out / "tier3.txt"))
    assert sum(k.startswith("ZORVIX") for k in tier3) == 22   # whole large op
    assert not any(k.startswith("SMALLMK") for k in tier3)     # small op excluded
    assert json.loads((out / "_manifest.json").read_text())["tier3"]["n_marks"] == 22


def test_origin_list_keys_marks_by_registered_owner_country(cfg, ledger):
    # An origin filter, NOT a tier: distinct mark keys whose filing lists the
    # owner country. Keyed on country of record only - no junk gate applies.
    cases = [
        make_case_file("80000001", mark="JEEFOME", owner_country="CN"),
        make_case_file("80000002", mark="SHENLUX", owner_country="CN"),
        make_case_file("80000003", mark="LIBERTY FORGE", owner_country="US"),
    ]
    candidate = _build(cfg, ledger, cases)
    gold = cfg.data_root / "gold"
    scorer = CoinedScorer(order=3).fit(["liberty", "forge", "silver"] * 40)
    mark_features.build(candidate, gold, scorer=scorer, log=lambda *a: None)

    out = detector_lists.build(candidate, gold / mark_features.TABLE, gold,
                               log=lambda *a: None)

    origin_cn = set(_lines(out / "origin_cn.txt"))
    assert origin_cn == {"JEEFOME", "SHENLUX"}       # CN owners only
    assert "LIBERTYFORGE" not in origin_cn           # US owner excluded

    origins = json.loads((out / "_manifest.json").read_text())["origins"]
    assert origins["lists"]["CN"] == {
        "file": "origin_cn.txt", "n_marks": 2, "label": "Origin - China"}
