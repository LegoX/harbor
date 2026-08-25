"""Load evaluation report metadata and derive per-instance resolved status."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import PipelineConfig

logger = logging.getLogger(__name__)


def resolve_instance_id(
    trial_dir: Path,
    *,
    trial_result_file: str = "result.json",
    result: dict | None = None,
) -> str:
    """Resolve instance_id from a Harbor trial directory.

    Prefer ``task_name`` from the trial result file (full SWE-bench-Pro id);
    fall back to the short form derived from the trial directory name.
    """
    if result is None:
        result_path = trial_dir / trial_result_file
        if result_path.exists():
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
            except Exception:
                logger.debug(
                    "Could not read task_name from %s", result_path, exc_info=True
                )
                result = {}
        else:
            result = {}

    task_name = result.get("task_name") if result else None
    if task_name:
        return str(task_name)
    return trial_dir.name.rsplit("__", 1)[0]


def load_report_metadata(cfg: PipelineConfig) -> dict:
    """Load the evaluation report metadata."""
    if cfg.data.trajectory_layout == "harbor_job":
        return load_harbor_report_metadata(cfg)

    report_path = cfg.data.report_path
    if report_path.exists():
        with open(report_path, "r") as f:
            return json.load(f)
    return {}


def _load_json(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def _resolved_from_verifier_report(report_path: Path, instance_id: str) -> bool | None:
    if not report_path.exists():
        return None
    try:
        report = _load_json(report_path)
        entry = report.get(instance_id)
        if isinstance(entry, dict) and "resolved" in entry:
            return bool(entry["resolved"])
    except Exception:
        logger.warning("Could not parse verifier report %s", report_path, exc_info=True)
    return None


def load_harbor_report_metadata(cfg: PipelineConfig) -> dict:
    """Build OpenHands-style metadata from Harbor trial artifacts."""
    log_dir = cfg.data.log_dir_path
    completed_ids: set[str] = set()
    resolved_ids: set[str] = set()
    error_ids: set[str] = set()

    if not log_dir.exists():
        raise FileNotFoundError(f"Harbor job dir not found: {log_dir}")

    for trial_dir in sorted(p for p in log_dir.iterdir() if p.is_dir()):
        result_path = trial_dir / cfg.data.trial_result_file
        result = {}
        if result_path.exists():
            try:
                result = _load_json(result_path)
            except Exception:
                logger.warning(
                    "Could not parse trial result %s", result_path, exc_info=True
                )

        instance_id = resolve_instance_id(
            trial_dir,
            trial_result_file=cfg.data.trial_result_file,
            result=result,
        )
        exception_info = result.get("exception_info") if result else None
        reward = (
            ((result.get("verifier_result") or {}).get("rewards", {}).get("reward"))
            if result
            else None
        )

        verifier_resolved = _resolved_from_verifier_report(
            trial_dir / cfg.data.trial_report_subpath,
            instance_id,
        )

        if reward is not None or verifier_resolved is not None:
            completed_ids.add(instance_id)
        if verifier_resolved is True or reward == 1.0:
            resolved_ids.add(instance_id)
        if exception_info and instance_id not in resolved_ids:
            error_ids.add(instance_id)

    return {
        "completed_ids": sorted(completed_ids),
        "resolved_ids": sorted(resolved_ids),
        "error_ids": sorted(error_ids),
        "empty_patch_ids": [],
    }


def identify_instances(
    report_metadata: dict, cfg: PipelineConfig
) -> tuple[set[str], set[str]]:
    """Identify failed and resolved instance IDs based on config.

    Returns (failed_ids, resolved_ids).
    """
    resolved_ids = set(report_metadata.get("resolved_ids", []))
    all_ids = set(report_metadata.get("completed_ids", []))

    error_ids = (
        set(report_metadata.get("error_ids", []))
        if cfg.analysis.include_errors
        else set()
    )
    empty_patch_ids = (
        set(report_metadata.get("empty_patch_ids", []))
        if cfg.analysis.include_empty_patch
        else set()
    )

    failed = (
        (all_ids - resolved_ids)
        | (error_ids - resolved_ids)
        | (empty_patch_ids - resolved_ids)
    )
    if not cfg.analysis.include_resolved:
        resolved_ids = set()

    return failed, resolved_ids


def augment_empty_patch_metadata(report_metadata: dict, trajectories: dict) -> None:
    empty_ids = set(report_metadata.get("empty_patch_ids", []))
    for instance_id, trajectory in trajectories.items():
        if not trajectory.model_patch.strip():
            empty_ids.add(instance_id)
    report_metadata["empty_patch_ids"] = sorted(empty_ids)


def build_instance_records(
    cfg: PipelineConfig,
    trajectories: dict | None = None,
) -> list[dict]:
    """Build minimal instance records (instance_id + resolved) from job metadata."""
    report_metadata = load_report_metadata(cfg)
    if trajectories is not None:
        augment_empty_patch_metadata(report_metadata, trajectories)

    failed_ids, resolved_ids = identify_instances(report_metadata, cfg)
    records: list[dict] = []
    for instance_id in sorted(failed_ids):
        records.append({"instance_id": instance_id, "resolved": False})
    for instance_id in sorted(resolved_ids):
        records.append({"instance_id": instance_id, "resolved": True})
    return records
