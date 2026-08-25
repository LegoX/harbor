import json
from pathlib import Path

from src.config import PipelineConfig
from src.pipeline import _load_instance_sets_for_optional_stages


def test_load_instance_sets_from_jsonl(tmp_path: Path):
    instances_path = tmp_path / "instances.jsonl"
    records = [
        {"instance_id": "a", "resolved": False},
        {"instance_id": "b", "resolved": True},
        {"instance_id": "c", "resolved": False},
    ]
    with open(instances_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    failed, resolved, resolved_ids = _load_instance_sets_for_optional_stages(
        PipelineConfig(), instances_path
    )
    assert failed == ["a", "c"]
    assert resolved == ["b"]
    assert resolved_ids == {"b"}
