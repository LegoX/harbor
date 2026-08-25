"""Multi-axis labeler: combine deterministic features + judge into labels."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.features.localization import LocalizationFeatures
from src.features.pathology import PathologyFlags
from src.hack_detector.reward_hack import HackResult
from src.judge.tier1_judge import Tier1Judgment

logger = logging.getLogger(__name__)


# Axis value enums
LOCALIZATION_VALUES = {"hit", "partial", "miss"}
DIAGNOSIS_VALUES = {"correct", "partial", "wrong"}
IMPLEMENTATION_VALUES = {
    "correct",
    "partial_fix",
    "wrong_logic",
    "wrong_location",
    "no_edit",
}
TOOL_USAGE_VALUES = {"ok", "suboptimal", "poor"}
LONG_HORIZON_VALUES = {"ok", "premature_stop", "truncation", "max_iter_reached"}

# Flag values
FLAG_VALUES = {
    "hack",
    "alternative_fix",
    "environment_noise",
    "empty_patch",
    "error_instance",
    "missing_gold",
}


@dataclass
class AxisLabels:
    """Multi-axis labels for a single instance."""

    localization: str  # hit, partial, miss
    diagnosis: str  # correct, partial, wrong
    implementation: str  # correct, partial_fix, wrong_logic, wrong_location, no_edit
    tool_usage: str  # ok, suboptimal, poor
    long_horizon: str  # ok, premature_stop, truncation, max_iter_reached


@dataclass
class InstanceLabels:
    """Complete label set for a single instance."""

    axes: AxisLabels
    primary_failure: str
    secondary_failures: list[str]
    flags: list[str]
    correctness_verdict: str  # V1-V5
    evidence_step_ids: list[int]


def _label_localization(
    loc_features: LocalizationFeatures, judgment: Tier1Judgment
) -> str:
    """Determine localization axis label."""
    # Combine C1/C2 with judge
    if judgment.localization_quality == "hit" or (
        loc_features.C1_file_read and loc_features.C2_func_read
    ):
        return "hit"
    elif judgment.localization_quality == "partial" or loc_features.C1_file_read:
        return "partial"
    else:
        return "miss"


def _label_diagnosis(judgment: Tier1Judgment) -> str:
    """Determine diagnosis axis label."""
    return judgment.behavioral_understanding


def _label_implementation(
    loc_features: LocalizationFeatures, judgment: Tier1Judgment
) -> str:
    """Determine implementation axis label."""
    # Combine C3/C4 with judge
    if judgment.implementation_quality == "no_edit":
        return "no_edit"
    if judgment.implementation_quality == "correct":
        return "correct"
    if loc_features.C3_file_alignment and loc_features.C4_func_alignment:
        # Model touched the right file and function but still failed
        if judgment.implementation_quality in ("wrong_logic", "partial_fix"):
            return judgment.implementation_quality
        return "partial_fix"
    if loc_features.C3_file_alignment and not loc_features.C4_func_alignment:
        return "wrong_logic"
    if not loc_features.C3_file_alignment:
        return "wrong_location"
    return judgment.implementation_quality


def _label_tool_usage(pathology: PathologyFlags, judgment: Tier1Judgment) -> str:
    """Determine tool_usage axis label."""
    if judgment.tool_usage_health == "poor" or pathology.tool_error_storm:
        return "poor"
    if judgment.tool_usage_health == "suboptimal" or pathology.loop_detected:
        return "suboptimal"
    return "ok"


def _label_long_horizon(
    pathology: PathologyFlags, loc_features: LocalizationFeatures
) -> str:
    """Determine long_horizon axis label."""
    if pathology.context_truncation:
        return "truncation"
    if pathology.premature_stop:
        return "premature_stop"
    # Check if max iterations reached
    if loc_features.max_iterations > 0:
        if loc_features.trajectory_length >= loc_features.max_iterations - 2:
            return "max_iter_reached"
    return "ok"


def _determine_primary_failure(axes: AxisLabels, flags: list[str]) -> str:
    """Determine primary failure using earliest-causal-first with flag priority."""
    # Flag priority: hack and environment override
    if "hack" in flags:
        return "hack"
    if "environment_noise" in flags:
        return "environment"
    if "missing_gold" in flags:
        return "error"
    if "empty_patch" in flags:
        return "empty_patch"
    if "error_instance" in flags:
        return "error"

    # Earliest causal: localization -> diagnosis -> implementation -> tool_usage -> long_horizon
    if axes.localization == "miss":
        return "localization"
    if axes.localization == "partial":
        return "localization"
    if axes.diagnosis == "wrong":
        return "diagnosis"
    if axes.diagnosis == "partial":
        return "diagnosis"
    if axes.implementation in ("wrong_logic", "wrong_location", "no_edit"):
        return "implementation"
    if axes.implementation == "partial_fix":
        return "implementation"
    if axes.tool_usage in ("poor", "suboptimal"):
        return "tool_usage"
    if axes.long_horizon != "ok":
        return "long_horizon"

    # Fallback
    return "implementation"


def _determine_secondary_failures(axes: AxisLabels, primary: str) -> list[str]:
    """Determine secondary failures (non-ok axes that aren't the primary)."""
    secondary = []

    if primary != "localization" and axes.localization in ("miss", "partial"):
        secondary.append("localization")
    if primary != "diagnosis" and axes.diagnosis in ("wrong", "partial"):
        secondary.append("diagnosis")
    if primary != "implementation" and axes.implementation in (
        "wrong_logic",
        "wrong_location",
        "no_edit",
        "partial_fix",
    ):
        secondary.append("implementation")
    if primary != "tool_usage" and axes.tool_usage in ("poor", "suboptimal"):
        secondary.append("tool_usage")
    if primary != "long_horizon" and axes.long_horizon != "ok":
        secondary.append("long_horizon")

    return secondary


def _determine_correctness_verdict(axes: AxisLabels, flags: list[str]) -> str:
    """Determine correctness verdict V1-V5.

    V1: Correct fix (should have passed - possible test/env issue)
    V2: Functionally equivalent alternative fix
    V3: Partially correct (right file, wrong logic or partial fix)
    V4: Wrong fix (wrong file or wrong approach)
    V5: No meaningful attempt
    """
    if "hack" in flags:
        return "V4"
    if "alternative_fix" in flags:
        return "V2"
    if axes.implementation == "correct":
        return "V1"
    if axes.implementation == "partial_fix":
        return "V3"
    if axes.implementation in ("wrong_logic", "wrong_location"):
        return "V4"
    if axes.implementation == "no_edit":
        return "V5"
    return "V4"


def label_instance(
    loc_features: LocalizationFeatures,
    pathology: PathologyFlags,
    hack_result: HackResult,
    judgment: Tier1Judgment,
    is_empty_patch: bool = False,
    is_error_instance: bool = False,
    is_resolved: bool = False,
    has_trajectory_error: bool = False,
    missing_gold: bool = False,
) -> InstanceLabels:
    """Combine all signals into multi-axis labels."""
    # Build flags
    flags = []
    if hack_result.verdict in ("confirmed_hack", "suspected_hack"):
        flags.append("hack")
    if missing_gold:
        flags.append("missing_gold")
    if is_empty_patch:
        flags.append("empty_patch")
    if is_error_instance:
        flags.append("error_instance")
    if has_trajectory_error and not is_error_instance and not is_empty_patch:
        flags.append("environment_noise")
    if (
        is_resolved
        and loc_features.diff_hunk_overlap < 0.5
        and judgment.implementation_quality != "no_edit"
    ):
        flags.append("alternative_fix")

    # Build axis labels
    axes = AxisLabels(
        localization=_label_localization(loc_features, judgment),
        diagnosis=_label_diagnosis(judgment),
        implementation=_label_implementation(loc_features, judgment),
        tool_usage=_label_tool_usage(pathology, judgment),
        long_horizon=_label_long_horizon(pathology, loc_features),
    )

    # Primary and secondary failures
    primary = _determine_primary_failure(axes, flags)
    secondary = _determine_secondary_failures(axes, primary)

    # Correctness verdict
    verdict = _determine_correctness_verdict(axes, flags)

    # Evidence step IDs
    evidence_ids = list(judgment.evidence_step_ids)

    return InstanceLabels(
        axes=axes,
        primary_failure=primary,
        secondary_failures=secondary,
        flags=flags,
        correctness_verdict=verdict,
        evidence_step_ids=evidence_ids,
    )
