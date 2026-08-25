import logging
from pathlib import Path

from src.config import load_config


def test_load_config_warns_on_unknown_keys(tmp_path: Path, caplog):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data:
  log_dir: "logs/test"
  unknown_data_key: true
unknown_top_level: 1
scaffold: "test"
""",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_config(str(config_path))
    assert cfg.scaffold == "test"
    assert any("unknown_data_key" in r.message for r in caplog.records)
    assert any("unknown_top_level" in r.message for r in caplog.records)
