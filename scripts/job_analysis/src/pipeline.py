"""Pipeline orchestrator: connects all stages end-to-end."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src.aggregator.report import aggregate, write_report, write_comparison_report
from src.config import PipelineConfig
from src.features.localization import extract_localization_features
from src.features.pathology import detect_pathologies
from src.hack_detector.reward_hack import detect_reward_hack, HackResult
from src.judge.tier1_judge import judge_instance
from src.labeler.axis_labeler import label_instance
from src.parser.patch_parser import parse_patch
from src.parser.test_parser import load_gold_instances, load_harbor_gold_instances
from src.parser.trajectory_parser import load_trajectories, load_harbor_trajectories
from src.task_analysis.features import extract_task_features, TaskDifficultyFeatures
from src.task_analysis.classifier import classify_task, TaskClassification
from src.task_analysis.report import aggregate_task_analysis, write_task_analysis_report
from src.instance_analysis import load_instance_records, run_instance_analysis
from src.report_metadata import (
    augment_empty_patch_metadata,
    identify_instances,
    load_report_metadata,
)

logger = logging.getLogger(__name__)


def _load_trajectory_data(cfg: PipelineConfig) -> dict:
    """Load trajectories using the configured input layout."""
    if cfg.data.trajectory_layout == "harbor_job":
        return load_harbor_trajectories(
            cfg.data.log_dir_path,
            trajectory_subpath=cfg.data.trajectory_subpath,
            max_iterations_default=cfg.data.max_iterations_default,
            trial_result_file=cfg.data.trial_result_file,
        )
    return load_trajectories(cfg.data.trajectory_path)


def _load_gold_data(cfg: PipelineConfig) -> dict:
    """Load gold data from JSONL or Harbor dataset task configs."""
    if cfg.data.gold_source == "harbor_dataset":
        return load_harbor_gold_instances(cfg.data.dataset_dir_path)
    return load_gold_instances(cfg.data.test_path)


def _is_empty_patch(patch: str) -> bool:
    """Check if a patch is empty or whitespace-only."""
    return not patch or not patch.strip()


def process_instance(
    instance_id: str,
    trajectory,
    gold_instance,
    cfg: PipelineConfig,
    resolved: bool = False,
) -> dict:
    """Process a single instance through stages 3-5 (features, judge, labeling)."""
    logger.debug("Processing %s (resolved=%s)", instance_id, resolved)

    missing_gold = gold_instance is None
    if missing_gold:
        logger.warning(
            "No gold data for %s; gold-aware features will be unavailable", instance_id
        )

    # Parse patches
    gold_patch_text = gold_instance.patch if gold_instance else ""
    model_patch_text = trajectory.model_patch

    gold_info = parse_patch(gold_patch_text)
    model_info = parse_patch(model_patch_text)

    # Stage 3: Deterministic features
    loc_features = extract_localization_features(trajectory, gold_info, model_info)
    pathology = detect_pathologies(
        trajectory,
        loop_threshold=cfg.features.loop_threshold,
        premature_stop_threshold=cfg.features.premature_stop_threshold,
        tool_error_storm_threshold=cfg.features.tool_error_storm_threshold,
    )

    # Reward hack detection (optional)
    if cfg.hack_detector.enabled:
        hack_result = detect_reward_hack(
            model_info,
            model_patch_text,
            gold_instance.test_patch if gold_instance else "",
        )
    else:
        hack_result = HackResult(
            verdict="not_hack",
            hacks_detected=[],
            evidence=[],
            confidence=1.0,
        )

    # Build deterministic features dict for judge
    det_features = {
        "C1_file_read": loc_features.C1_file_read,
        "C2_func_read": loc_features.C2_func_read,
        "C3_file_alignment": loc_features.C3_file_alignment,
        "C4_func_alignment": loc_features.C4_func_alignment,
        "C5_test_executed": loc_features.C5_test_executed,
        "diff_hunk_overlap": loc_features.diff_hunk_overlap,
        "diff_line_delta": loc_features.diff_line_delta,
        "diff_file_delta": loc_features.diff_file_delta,
        "modified_test_file": loc_features.modified_test_file,
        "trajectory_length": loc_features.trajectory_length,
        "max_iterations": loc_features.max_iterations,
        "loop_detected": pathology.loop_detected,
        "premature_stop": pathology.premature_stop,
        "tool_error_storm": pathology.tool_error_storm,
        "context_truncation": pathology.context_truncation,
        "model_patch_exists": bool(model_patch_text and model_patch_text.strip()),
    }

    # Stage 4: Tier-1 Judge
    judgment = judge_instance(
        trajectory,
        model_patch_text,
        gold_patch_text,
        det_features,
        cfg,
        resolved=resolved,
    )

    # Stage 5: Multi-axis labeling
    is_empty = _is_empty_patch(model_patch_text)
    # Only flag as error_instance if there's an error AND no meaningful patch
    is_error = trajectory.error is not None and not model_patch_text.strip()
    has_trajectory_error = trajectory.error is not None

    labels = label_instance(
        loc_features,
        pathology,
        hack_result,
        judgment,
        is_empty_patch=is_empty,
        is_error_instance=is_error,
        is_resolved=resolved,
        has_trajectory_error=has_trajectory_error,
        missing_gold=missing_gold,
    )

    # Build output record
    result = {
        "instance_id": instance_id,
        "resolved": resolved,
        "scaffold": cfg.scaffold,
        "model": cfg.model,
        "taxonomy_version": cfg.taxonomy_version,
        "judge_version": cfg.judge_version,
        "trajectory_metadata": {
            "trial_name": trajectory.metadata.get("trial_name"),
            "trajectory_layout": trajectory.metadata.get("trajectory_layout"),
            "model_patch_source": trajectory.metadata.get("model_patch_source"),
            "model_patch_edit_count": trajectory.metadata.get("model_patch_edit_count"),
            "model_patch_undo_count": trajectory.metadata.get("model_patch_undo_count"),
            "model_patch_files": trajectory.metadata.get("model_patch_files", []),
        },
        "deterministic_features": det_features,
        "tier1_labels": {
            "localization_quality": judgment.localization_quality,
            "behavioral_understanding": judgment.behavioral_understanding,
            "implementation_quality": judgment.implementation_quality,
            "tool_usage_health": judgment.tool_usage_health,
            "failure_attribution": judgment.failure_attribution,
            "reasoning": judgment.reasoning,
        },
        "axes": {
            "localization": labels.axes.localization,
            "diagnosis": labels.axes.diagnosis,
            "implementation": labels.axes.implementation,
            "tool_usage": labels.axes.tool_usage,
            "long_horizon": labels.axes.long_horizon,
        },
        "primary_failure": labels.primary_failure,
        "secondary_failures": labels.secondary_failures,
        "flags": labels.flags,
        "correctness_verdict": labels.correctness_verdict,
        "evidence_step_ids": labels.evidence_step_ids,
    }

    # Add hack details if detected
    if hack_result.hacks_detected:
        result["hack_details"] = {
            "verdict": hack_result.verdict,
            "hacks_detected": hack_result.hacks_detected,
            "evidence": hack_result.evidence,
            "confidence": hack_result.confidence,
        }

    # Add pathology details if detected
    if (
        pathology.loop_detected
        or pathology.premature_stop
        or pathology.tool_error_storm
        or pathology.context_truncation
    ):
        result["pathology_details"] = {
            "loop_detected": pathology.loop_detected,
            "loop_details": pathology.loop_details,
            "premature_stop": pathology.premature_stop,
            "premature_stop_remaining_frac": pathology.premature_stop_remaining_frac,
            "tool_error_storm": pathology.tool_error_storm,
            "tool_error_storm_count": pathology.tool_error_storm_count,
            "context_truncation": pathology.context_truncation,
        }

    return result


def _process_instance_list(
    instance_ids: list[str],
    trajectories: dict,
    gold_instances: dict,
    cfg: PipelineConfig,
    resolved: bool,
    label: str,
) -> list[dict]:
    """Process a list of instances and return results."""
    results = []
    for i, iid in enumerate(instance_ids):
        traj = trajectories[iid]
        gold = gold_instances.get(iid)

        try:
            result = process_instance(iid, traj, gold, cfg, resolved=resolved)
            results.append(result)
        except Exception as e:
            logger.error("Error processing %s: %s", iid, e, exc_info=True)
            results.append(
                {
                    "instance_id": iid,
                    "resolved": resolved,
                    "scaffold": cfg.scaffold,
                    "model": cfg.model,
                    "taxonomy_version": cfg.taxonomy_version,
                    "judge_version": cfg.judge_version,
                    "deterministic_features": {},
                    "tier1_labels": {},
                    "axes": {
                        "localization": "miss",
                        "diagnosis": "wrong",
                        "implementation": "no_edit",
                        "tool_usage": "ok",
                        "long_horizon": "ok",
                    },
                    "primary_failure": "error",
                    "secondary_failures": [],
                    "flags": ["error_instance"],
                    "correctness_verdict": "V5",
                    "evidence_step_ids": [],
                    "processing_error": str(e),
                }
            )

        if (i + 1) % 50 == 0:
            logger.info("Processed %d/%d %s instances", i + 1, len(instance_ids), label)

    return results


def _any_optional_stage_enabled(cfg: PipelineConfig) -> bool:
    return (
        cfg.task_analysis.enabled
        or cfg.traj_analysis.enabled
        or cfg.instance_analysis.enabled
    )


def _identify_available_instances(
    cfg: PipelineConfig,
) -> tuple[list[str], list[str], set[str]]:
    """Return (available_failed, available_resolved, resolved_ids)."""
    trajectories = _load_trajectory_data(cfg)
    report_metadata = load_report_metadata(cfg)
    augment_empty_patch_metadata(report_metadata, trajectories)

    failed_ids, resolved_ids = identify_instances(report_metadata, cfg)
    available_failed = sorted(failed_ids & set(trajectories.keys()))
    available_resolved = sorted(resolved_ids & set(trajectories.keys()))
    return available_failed, available_resolved, resolved_ids


def _load_instance_sets_for_optional_stages(
    cfg: PipelineConfig,
    instances_path: Path,
) -> tuple[list[str], list[str], set[str]]:
    """Load failed/resolved instance lists for optional stages.

    When ``instances.jsonl`` already exists (typical with ``skip_main_pipeline``),
    read instance IDs directly instead of reloading all trajectories.
    """
    if instances_path.is_file():
        failed: list[str] = []
        resolved: list[str] = []
        with open(instances_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                iid = rec["instance_id"]
                if rec.get("resolved"):
                    resolved.append(iid)
                else:
                    failed.append(iid)
        logger.info(
            "Using %d failed / %d resolved instance IDs from %s (skip trajectory reload)",
            len(failed),
            len(resolved),
            instances_path,
        )
        return sorted(failed), sorted(resolved), set(resolved)

    logger.info(
        "%s not found; loading trajectories to discover available instances",
        instances_path,
    )
    return _identify_available_instances(cfg)


def _run_main_pipeline(
    cfg: PipelineConfig,
) -> tuple[Path, list[str], list[str], set[str], dict]:
    """Run stages 1-6. Returns (instances_path, available_failed, available_resolved, resolved_ids, gold_instances)."""
    logger.info("Loading trajectories...")
    trajectories = _load_trajectory_data(cfg)

    logger.info("Loading gold instances...")
    gold_instances = _load_gold_data(cfg)

    report_metadata = load_report_metadata(cfg)
    augment_empty_patch_metadata(report_metadata, trajectories)

    failed_ids, resolved_ids = identify_instances(report_metadata, cfg)
    logger.info(
        "Failed instances: %d, Resolved instances: %d",
        len(failed_ids),
        len(resolved_ids),
    )

    available_failed = sorted(failed_ids & set(trajectories.keys()))
    available_resolved = sorted(resolved_ids & set(trajectories.keys()))
    logger.info(
        "Available failed instances: %d, Available resolved instances: %d",
        len(available_failed),
        len(available_resolved),
    )

    if not available_failed and not available_resolved:
        logger.warning("No instances to analyze!")
        output_dir = cfg.output.output_dir
        return (
            output_dir / cfg.output.instances_jsonl,
            [],
            [],
            resolved_ids,
            gold_instances,
        )

    failed_results = []
    missing_gold_count = 0
    if available_failed:
        logger.info("Processing %d failed instances...", len(available_failed))
        failed_results = _process_instance_list(
            available_failed,
            trajectories,
            gold_instances,
            cfg,
            resolved=False,
            label="failed",
        )
        logger.info("Processed %d failed instances", len(failed_results))
        missing_gold_count += sum(
            1 for r in failed_results if "missing_gold" in r.get("flags", [])
        )

    resolved_results = []
    if available_resolved:
        logger.info("Processing %d resolved instances...", len(available_resolved))
        resolved_results = _process_instance_list(
            available_resolved,
            trajectories,
            gold_instances,
            cfg,
            resolved=True,
            label="resolved",
        )
        logger.info("Processed %d resolved instances", len(resolved_results))
        missing_gold_count += sum(
            1 for r in resolved_results if "missing_gold" in r.get("flags", [])
        )

    if missing_gold_count:
        logger.warning(
            "%d instances missing gold data (gold-aware features unavailable)",
            missing_gold_count,
        )

    output_dir = cfg.output.output_dir
    all_results = failed_results + resolved_results

    if failed_results:
        logger.info("Aggregating failed results...")
        failed_report = aggregate(failed_results)
        write_report(failed_report, output_dir, failed_results, suffix="failed")

    if resolved_results:
        logger.info("Aggregating resolved results...")
        resolved_report = aggregate(resolved_results)
        write_report(resolved_report, output_dir, resolved_results, suffix="resolved")

    if failed_results and resolved_results:
        logger.info("Generating comparison report...")
        write_comparison_report(
            failed_report,
            resolved_report,
            output_dir,
            failed_results,
            resolved_results,
        )

    instances_path = output_dir / cfg.output.instances_jsonl
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(instances_path, "w") as f:
        for result in all_results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    logger.info(
        "Wrote %d combined instance results to %s", len(all_results), instances_path
    )

    return (
        instances_path,
        available_failed,
        available_resolved,
        resolved_ids,
        gold_instances,
    )


def _run_task_analysis_stage(
    cfg: PipelineConfig,
    output_dir: Path,
    available_failed: list[str],
    available_resolved: list[str],
    resolved_ids: set[str],
    gold_instances: dict,
) -> None:
    logger.info("Running task analysis...")
    all_instance_ids = available_failed + available_resolved
    task_features_list: list[TaskDifficultyFeatures] = []
    task_classifications_list: list[TaskClassification] = []

    for iid in all_instance_ids:
        gold = gold_instances.get(iid)
        if not gold:
            continue
        try:
            tf = extract_task_features(
                instance_id=iid,
                gold_patch_text=gold.patch,
                problem_statement=gold.problem_statement,
                fail_to_pass=gold.fail_to_pass,
                pass_to_pass=gold.pass_to_pass,
                test_patch=gold.test_patch,
                swebench_difficulty=gold.difficulty,
            )
            task_features_list.append(tf)

            tc = classify_task(
                instance_id=iid,
                repo=gold.repo,
                problem_statement=gold.problem_statement,
                gold_patch_text=gold.patch,
            )
            task_classifications_list.append(tc)
        except Exception as e:
            logger.error("Error in task analysis for %s: %s", iid, e, exc_info=True)

    if task_features_list:
        task_report = aggregate_task_analysis(
            task_features_list,
            task_classifications_list,
            resolved_ids,
        )
        write_task_analysis_report(
            task_report,
            output_dir,
            task_features_list,
            task_classifications_list,
        )
        logger.info(
            "Task analysis complete: %d instances analyzed", len(task_features_list)
        )


def _run_traj_analysis_stage(cfg: PipelineConfig, output_dir: Path) -> None:
    try:
        from src.traj_analysis.comparison import (
            run_comparison,
            run_openhands_jsonl_comparison,
        )
    except (ImportError, RuntimeError) as e:
        logger.error(
            "Cannot import local traj_analysis comparison module (%s).",
            e,
        )
        return

    rs_out_dir = output_dir / cfg.traj_analysis.out_subdir
    logger.info("Running trajectory score comparison -> %s", rs_out_dir)
    try:
        if cfg.data.trajectory_layout == "harbor_job":
            run_comparison(
                cfg.data.log_dir_path,
                rs_out_dir,
                max_instances=cfg.traj_analysis.max_instances,
                trajectory_subpath=cfg.data.trajectory_subpath,
                trial_result_file=cfg.data.trial_result_file,
                trial_report_subpath=cfg.data.trial_report_subpath,
            )
        elif cfg.data.trajectory_layout == "openhands_jsonl":
            run_openhands_jsonl_comparison(
                cfg.data.trajectory_path,
                cfg.data.report_path,
                rs_out_dir,
                max_instances=cfg.traj_analysis.max_instances,
            )
        else:
            logger.warning(
                "traj_analysis is enabled but trajectory_layout=%s is not supported. Skipping.",
                cfg.data.trajectory_layout,
            )
    except Exception:
        logger.exception("traj_analysis stage failed")


def _run_instance_analysis_stage(
    cfg: PipelineConfig,
    output_dir: Path,
    instances_path: Path,
) -> None:
    if not cfg.data.dataset_dir:
        logger.warning(
            "instance_analysis is enabled but data.dataset_dir is unset; skipping."
        )
        return

    ta_out_dir = output_dir / cfg.instance_analysis.out_subdir
    logger.info("Running instance analysis -> %s", ta_out_dir)
    try:
        dataset_dir = Path(cfg.data.dataset_dir)
        if instances_path.is_file():
            run_instance_analysis(
                instances_jsonl=instances_path,
                dataset_dir=dataset_dir,
                out_dir=ta_out_dir,
            )
        else:
            logger.info(
                "instances.jsonl not found at %s; deriving records from job metadata",
                instances_path,
            )
            instances = load_instance_records(cfg)
            if not instances:
                logger.warning(
                    "No instances found in job metadata; skipping instance_analysis."
                )
                return
            run_instance_analysis(
                instances=instances,
                dataset_dir=dataset_dir,
                out_dir=ta_out_dir,
            )
    except Exception:
        logger.exception("instance_analysis stage failed")


def run_pipeline(cfg: PipelineConfig) -> None:
    """Run the analysis pipeline for both failed and resolved instances."""
    start_time = time.time()
    logger.info("Starting SWE-bench Analysis Pipeline")

    output_dir = cfg.output.output_dir
    instances_path = output_dir / cfg.output.instances_jsonl
    available_failed: list[str] = []
    available_resolved: list[str] = []
    resolved_ids: set[str] = set()
    gold_instances: dict = {}

    if cfg.skip_main_pipeline:
        logger.info("Skipping main pipeline (stages 1-6)")
        if not _any_optional_stage_enabled(cfg):
            logger.warning(
                "skip_main_pipeline=true but no optional analysis stages are enabled; nothing to do."
            )
            return
    else:
        (
            instances_path,
            available_failed,
            available_resolved,
            resolved_ids,
            gold_instances,
        ) = _run_main_pipeline(cfg)

    if cfg.task_analysis.enabled:
        if cfg.skip_main_pipeline:
            logger.info("Loading data for task analysis...")
            gold_instances = _load_gold_data(cfg)
            available_failed, available_resolved, resolved_ids = (
                _load_instance_sets_for_optional_stages(cfg, instances_path)
            )
        _run_task_analysis_stage(
            cfg,
            output_dir,
            available_failed,
            available_resolved,
            resolved_ids,
            gold_instances,
        )

    if cfg.traj_analysis.enabled:
        _run_traj_analysis_stage(cfg, output_dir)

    if cfg.instance_analysis.enabled:
        _run_instance_analysis_stage(cfg, output_dir, instances_path)

    elapsed = time.time() - start_time
    logger.info("Pipeline completed in %.1f seconds", elapsed)
    logger.info("Output written to %s", output_dir)
