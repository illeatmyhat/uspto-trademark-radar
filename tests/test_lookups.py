"""The packaged lookup CSVs (silver-schema §6): present, well-formed,
provenance-pinned, and shipped by publish."""

import csv

from uspto_trademark_radar import lookups


def test_all_lookup_files_present_and_loadable():
    for name in lookups.LOOKUP_FILES:
        table = lookups.load(name)
        assert table, f"{name} is empty"


def test_codes_unique_and_descriptions_nonempty():
    for name in lookups.LOOKUP_FILES:
        path = lookups.lookup_dir() / name
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        # event codes repeat per mailing channel; their key is the pair
        # (code, event_type_cd), matching silver's event table
        keys = ([(r["code"], r["event_type_cd"]) for r in rows]
                if name == "event_codes.csv" else [r["code"] for r in rows])
        assert len(keys) == len(set(keys)), f"{name} has duplicate codes"
        assert all(r["code"].strip() for r in rows), f"{name} has blank codes"
        assert all(r["description"].strip() for r in rows), \
            f"{name} has blank descriptions"


def test_sources_pin_every_table():
    src = lookups.sources()
    for name in lookups.LOOKUP_FILES:
        assert name in src, f"_sources.json missing entry for {name}"
        assert src[name]["url"].startswith("https://"), name
        assert src[name]["retrieved"], name


def test_known_codes_decode():
    # Spot checks against codes the analysis relies on.
    status = lookups.load("status_codes.csv")
    assert "700" in status                       # registered
    drawing = lookups.load("drawing_codes.csv")
    assert "4" in drawing                        # standard character mark
    entity = lookups.load("entity_codes.csv")
    assert "01" in entity and "03" in entity     # individual, corporation
