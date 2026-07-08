import pytest

from uspto_trademark_radar.lookups import LOOKUP_FILES
from uspto_trademark_radar.publish import (
    publish_bronze,
    stage_lookups,
    write_bronze_card,
)


def test_bronze_card_content(tmp_path):
    card = write_bronze_card(tmp_path)
    text = card.read_text(encoding="utf-8")
    assert "TRTYRAP" in text and "TRTDXFAP" in text
    assert "append-only" in text
    assert "license_name: us-government-work" in text


def test_publish_bronze_requires_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="HF_TOKEN"):
        publish_bronze(tmp_path, "user/bronze")


def test_stage_lookups_copies_all_tables(tmp_path):
    dest = stage_lookups(tmp_path)
    assert dest == tmp_path / "lookups"
    for name in (*LOOKUP_FILES, "_sources.json"):
        assert (dest / name).exists(), name
