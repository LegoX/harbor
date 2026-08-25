import json
from pathlib import Path

from src.parser.trajectory_parser import load_harbor_trajectories


def _write_harbor_trial(job_dir: Path, trial_name: str, instance_id: str) -> None:
    trial_dir = job_dir / trial_name
    trajectory_dir = trial_dir / "agent"
    trajectory_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps({"task_name": instance_id}),
        encoding="utf-8",
    )
    (trajectory_dir / "litellm-trajectory.jsonl").write_text("", encoding="utf-8")


def test_load_harbor_trajectories_parallel(tmp_path: Path):
    _write_harbor_trial(tmp_path, "repo__pkg-1__attempt", "repo__pkg-1")
    _write_harbor_trial(tmp_path, "repo__pkg-2__attempt", "repo__pkg-2")

    trajectories = load_harbor_trajectories(tmp_path, max_workers=2)

    assert sorted(trajectories) == ["repo__pkg-1", "repo__pkg-2"]
    assert trajectories["repo__pkg-1"].metadata["trial_name"] == "repo__pkg-1__attempt"


def test_load_harbor_trajectories_parallel_duplicate_instance_is_ordered(
    tmp_path: Path,
):
    _write_harbor_trial(tmp_path, "repo__pkg-1__attempt-a", "repo__pkg-1")
    _write_harbor_trial(tmp_path, "repo__pkg-1__attempt-b", "repo__pkg-1")

    trajectories = load_harbor_trajectories(tmp_path, max_workers=2)

    assert sorted(trajectories) == ["repo__pkg-1"]
    assert trajectories["repo__pkg-1"].metadata["trial_name"] == (
        "repo__pkg-1__attempt-b"
    )
