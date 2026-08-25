"""Pathology detection: loop, premature stop, tool storm, truncation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.parser.trajectory_parser import Trajectory

logger = logging.getLogger(__name__)


@dataclass
class PathologyFlags:
    """Detected trajectory pathologies."""

    loop_detected: bool
    loop_details: str  # description of the loop
    premature_stop: bool
    premature_stop_remaining_frac: float
    tool_error_storm: bool
    tool_error_storm_count: int
    context_truncation: bool


def _detect_loop(trajectory: Trajectory, threshold: int = 3) -> tuple[bool, str]:
    """Detect if the agent loops on the same action N times.

    A loop is N consecutive steps with the same action_type and similar action_detail.
    """
    if len(trajectory.steps) < threshold:
        return False, ""

    consecutive = 1
    for i in range(1, len(trajectory.steps)):
        prev = trajectory.steps[i - 1]
        curr = trajectory.steps[i]

        if (
            prev.action_type == curr.action_type
            and prev.action_detail == curr.action_detail
        ):
            consecutive += 1
            if consecutive >= threshold:
                return True, (
                    f"Loop: {curr.action_type} '{curr.action_detail[:80]}' "
                    f"repeated {consecutive} times (step {curr.step_id})"
                )
        else:
            consecutive = 1

    # Also check for near-loops: same action_type + same file path but different detail
    file_actions = [
        s for s in trajectory.steps if s.action_type in ("file_view", "file_edit")
    ]
    file_counts: dict[str, int] = {}
    for s in file_actions:
        # Extract just the file path
        path = s.action_detail.strip()
        file_counts[path] = file_counts.get(path, 0) + 1

    for path, count in file_counts.items():
        if count >= threshold * 2:  # Higher threshold for file revisits
            return True, f"File revisit loop: '{path[:80]}' visited {count} times"

    return False, ""


def _detect_premature_stop(
    trajectory: Trajectory, threshold: float = 0.3
) -> tuple[bool, float]:
    """Detect if agent stopped with > threshold fraction of iterations remaining."""
    max_iter = trajectory.max_iterations
    if max_iter <= 0:
        return False, 0.0

    # Find the finish step
    finish_steps = [s for s in trajectory.steps if s.action_type == "finish"]
    if not finish_steps:
        # No explicit finish - may have been cut off
        used = len(trajectory.steps)
        remaining_frac = (max_iter - used) / max_iter
        if remaining_frac > threshold:
            return True, round(remaining_frac, 3)
        return False, round(remaining_frac, 3)

    # Check if finish happened early
    finish_step_id = finish_steps[0].step_id
    remaining_frac = (max_iter - finish_step_id) / max_iter
    if remaining_frac > threshold:
        return True, round(remaining_frac, 3)

    return False, round(remaining_frac, 3)


def _detect_tool_error_storm(
    trajectory: Trajectory, threshold: int = 5
) -> tuple[bool, int]:
    """Detect K consecutive error observations."""
    consecutive_errors = 0
    max_consecutive = 0

    for step in trajectory.steps:
        if step.is_error:
            consecutive_errors += 1
            max_consecutive = max(max_consecutive, consecutive_errors)
        else:
            consecutive_errors = 0

    return max_consecutive >= threshold, max_consecutive


def _detect_truncation(trajectory: Trajectory) -> bool:
    """Detect if context was truncated (heuristic)."""
    # Check for truncation indicators in observations
    truncation_patterns = [
        "truncated",
        "output truncated",
        "... (truncated)",
        "[Output truncated",
        "max tokens",
        "context window",
        "context length",
    ]
    for step in trajectory.steps:
        obs_lower = step.observation.lower()
        for pattern in truncation_patterns:
            if pattern in obs_lower:
                return True
        thought_lower = step.thought.lower()
        for pattern in truncation_patterns:
            if pattern in thought_lower:
                return True

    # Also check if trajectory ends abruptly without a finish action
    if trajectory.steps and trajectory.steps[-1].action_type != "finish":
        # Check if it's near the max iterations
        if trajectory.max_iterations > 0:
            used = len(trajectory.steps)
            if used >= trajectory.max_iterations - 2:
                return True

    return False


def detect_pathologies(
    trajectory: Trajectory,
    loop_threshold: int = 3,
    premature_stop_threshold: float = 0.3,
    tool_error_storm_threshold: int = 5,
) -> PathologyFlags:
    """Detect all trajectory pathologies."""
    loop_detected, loop_details = _detect_loop(trajectory, loop_threshold)
    premature_stop, remaining_frac = _detect_premature_stop(
        trajectory, premature_stop_threshold
    )
    tool_storm, storm_count = _detect_tool_error_storm(
        trajectory, tool_error_storm_threshold
    )
    truncation = _detect_truncation(trajectory)

    return PathologyFlags(
        loop_detected=loop_detected,
        loop_details=loop_details,
        premature_stop=premature_stop,
        premature_stop_remaining_frac=remaining_frac,
        tool_error_storm=tool_storm,
        tool_error_storm_count=storm_count,
        context_truncation=truncation,
    )
