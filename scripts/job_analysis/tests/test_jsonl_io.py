import json
from pathlib import Path

from src.traj_analysis.jsonl_io import load_jsonl, save_jsonl


def test_jsonl_roundtrip(tmp_path: Path):
    path = tmp_path / "data.jsonl"
    records = [{"id": 1}, {"id": 2}]
    save_jsonl(path, records)
    loaded = load_jsonl(path)
    assert loaded == records
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == 1
