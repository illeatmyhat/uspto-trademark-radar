from pathlib import Path

from uspto_trademark_radar.config import Config


def test_data_root_defaults_to_repo_data(monkeypatch):
    monkeypatch.delenv("TRADEMARK_RADAR_DATA_ROOT", raising=False)
    cfg = Config()
    assert cfg.data_root == cfg.repo_root / "data"
    # regression: Path("") is truthy, must not root the tree in the CWD
    assert cfg.data_root != Path(".")


def test_data_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEMARK_RADAR_DATA_ROOT", str(tmp_path / "elsewhere"))
    cfg = Config()
    assert cfg.data_root == tmp_path / "elsewhere"
    assert cfg.ledger_path == tmp_path / "elsewhere" / "ledger.sqlite"


def test_empty_env_override_falls_back(monkeypatch):
    monkeypatch.setenv("TRADEMARK_RADAR_DATA_ROOT", "")
    cfg = Config()
    assert cfg.data_root == cfg.repo_root / "data"
