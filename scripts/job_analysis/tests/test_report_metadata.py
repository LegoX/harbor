import json
from pathlib import Path

from src.config import PipelineConfig
from src.report_metadata import (
    identify_instances,
    load_harbor_report_metadata,
    resolve_instance_id,
)


def test_identify_instances_respects_include_resolved(tmp_path: Path):
    cfg = PipelineConfig()
    cfg.analysis.include_resolved = False
    metadata = {
        "completed_ids": ["a", "b"],
        "resolved_ids": ["a"],
        "error_ids": [],
        "empty_patch_ids": [],
    }
    failed, resolved = identify_instances(metadata, cfg)
    assert failed == {"b"}
    assert resolved == set()


def test_load_harbor_report_metadata_from_trials(tmp_path: Path):
    trial = tmp_path / "django__django-1__attempt"
    trial.mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "django__django-1",
                "verifier_result": {"rewards": {"reward": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    (trial / "verifier").mkdir()
    (trial / "verifier" / "report.json").write_text(
        json.dumps({"django__django-1": {"resolved": True}}),
        encoding="utf-8",
    )

    cfg = PipelineConfig()
    cfg.data.log_dir = str(tmp_path)
    metadata = load_harbor_report_metadata(cfg)
    assert "django__django-1" in metadata["resolved_ids"]
    assert "django__django-1" in metadata["completed_ids"]


def test_resolve_instance_id_prefers_task_name(tmp_path: Path):
    trial_dir = tmp_path / "short__attempt"
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(
        json.dumps({"task_name": "repo__pkg-42"}),
        encoding="utf-8",
    )
    assert resolve_instance_id(trial_dir) == "repo__pkg-42"


def test_resolve_instance_id_falls_back_to_directory_name(tmp_path: Path):
    trial_dir = tmp_path / "repo__pkg-42__attempt"
    trial_dir.mkdir()
    assert resolve_instance_id(trial_dir) == "repo__pkg-42"
