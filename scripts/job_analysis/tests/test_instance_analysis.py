import json
from pathlib import Path

from src.config import PipelineConfig
from src.instance_analysis.loader import load_instance_records
from src.instance_analysis.runner import run_instance_analysis


def test_instance_analysis_contingency_and_correlation(tmp_path: Path):
    dataset_dir = tmp_path / "dataset"
    for iid, resolved, label in [
        ("inst-a", True, "easy"),
        ("inst-b", False, "easy"),
        ("inst-c", True, "hard"),
    ]:
        inst_dir = dataset_dir / iid
        inst_dir.mkdir(parents=True)
        # instance_analysis reads the [metadata] block of task.toml, as written
        # by scripts/task_analysis/tag_task_metadata.py.
        (inst_dir / "task.toml").write_text(
            "[metadata]\n"
            f'difficulty = "{label}"\n'
            'tags = ["python", "library", "bugfix", "wrong-default"]\n',
            encoding="utf-8",
        )

    instances = [
        {"instance_id": "inst-a", "resolved": True},
        {"instance_id": "inst-b", "resolved": False},
        {"instance_id": "inst-c", "resolved": True},
    ]
    out_dir = tmp_path / "out"
    summary = run_instance_analysis(
        instances=instances,
        dataset_dir=dataset_dir,
        out_dir=out_dir,
    )

    assert summary["n_rows"] == 3
    assert (out_dir / "contingency_difficulty_label.csv").is_file()
    assert (out_dir / "correlations.json").is_file()
    assert "difficulty_score" in summary["correlations"]


def test_load_instance_records_from_harbor_metadata_without_trajectories(
    tmp_path: Path,
):
    resolved_trial = tmp_path / "django__django-1__attempt"
    resolved_trial.mkdir()
    (resolved_trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "django__django-1",
                "verifier_result": {"rewards": {"reward": 1.0}},
            }
        ),
        encoding="utf-8",
    )

    unresolved_trial = tmp_path / "sympy__sympy-2__attempt"
    unresolved_trial.mkdir()
    (unresolved_trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "sympy__sympy-2",
                "verifier_result": {"rewards": {"reward": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    cfg = PipelineConfig()
    cfg.data.trajectory_layout = "harbor_job"
    cfg.data.log_dir = str(tmp_path)
    cfg.analysis.include_resolved = True

    assert load_instance_records(cfg) == [
        {"instance_id": "sympy__sympy-2", "resolved": False},
        {"instance_id": "django__django-1", "resolved": True},
    ]


def test_load_instance_records_does_not_pass_trajectories(monkeypatch):
    cfg = PipelineConfig()

    def fake_build_instance_records(received_cfg):
        assert received_cfg is cfg
        return [{"instance_id": "inst-a", "resolved": False}]

    monkeypatch.setattr(
        "src.instance_analysis.loader.build_instance_records",
        fake_build_instance_records,
    )

    assert load_instance_records(cfg) == [{"instance_id": "inst-a", "resolved": False}]
