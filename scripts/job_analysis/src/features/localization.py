"""Localization checkpoints C1-C5 and diff statistics."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.parser.patch_parser import PatchInfo, compute_patch_overlap
from src.parser.trajectory_parser import Trajectory

logger = logging.getLogger(__name__)


@dataclass
class LocalizationFeatures:
    """Deterministic localization features C1-C5 plus diff stats."""

    # C1: Did agent view any gold-patch file?
    C1_file_read: bool
    # C2: Did agent view any gold-patch function/class?
    C2_func_read: bool
    # C3: Does model patch touch any gold-patch file?
    C3_file_alignment: bool
    # C4: Does model patch touch any gold-patch function/class?
    C4_func_alignment: bool
    # C5: Did agent run any test command?
    C5_test_executed: bool
    # Diff statistics
    diff_hunk_overlap: float
    diff_line_delta: int
    diff_file_delta: int
    modified_test_file: bool
    # Trajectory stats
    trajectory_length: int
    max_iterations: int
    # Overlap details
    file_overlap_count: int
    func_overlap_count: int


def _check_file_read(trajectory: Trajectory, gold_paths: set[str]) -> bool:
    """C1: Check if any gold-patch file path appears in file_view/file_edit actions."""
    for step in trajectory.steps:
        if step.action_type in ("file_view", "file_edit"):
            detail = _normalize_path_for_match(step.action_detail)
            for gold_path in gold_paths:
                if _paths_match(detail, gold_path):
                    return True
    return False


def _normalize_path_for_match(path: str) -> str:
    for prefix in ("/testbed/", "/workspace/", "/app/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path.strip().lstrip("/")


def _paths_match(detail: str, gold_path: str) -> bool:
    gold_norm = gold_path.strip().lstrip("/")
    if not detail or not gold_norm:
        return False
    if detail == gold_norm:
        return True
    return detail.endswith("/" + gold_norm)


def _func_name_in_text(func: str, text: str) -> bool:
    if not func or not text:
        return False
    return bool(re.search(rf"\b{re.escape(func)}\b", text))


def _check_func_read(trajectory: Trajectory, gold_funcs: set[str]) -> bool:
    """C2: Check if any gold-patch function/class name appears in view content or edit targets."""
    if not gold_funcs:
        return False

    for step in trajectory.steps:
        searchable = " ".join(
            part
            for part in (step.action_detail, step.observation, step.thought)
            if part
        )
        for func in gold_funcs:
            if _func_name_in_text(func, searchable):
                return True
    return False


def _check_file_alignment(model_info: PatchInfo, gold_paths: set[str]) -> bool:
    """C3: Whether model patch touches any gold-patch file."""
    return bool(model_info.file_paths & gold_paths)


def _check_func_alignment(model_info: PatchInfo, gold_funcs: set[str]) -> bool:
    """C4: Whether model patch touches any gold-patch function/class."""
    return bool(model_info.func_names & gold_funcs)


def _check_test_executed(trajectory: Trajectory) -> bool:
    """C5: Whether any terminal command runs tests."""
    test_patterns = re.compile(
        r"(pytest|unittest|nosetests|python\s+-m\s+test|"
        r"python\s+-m\s+pytest|\.\/runtests|test_|run\s+test)",
        re.IGNORECASE,
    )
    for step in trajectory.steps:
        if step.action_type == "terminal_cmd":
            if test_patterns.search(step.action_detail):
                return True
    return False


def extract_localization_features(
    trajectory: Trajectory,
    gold_info: PatchInfo,
    model_info: PatchInfo,
) -> LocalizationFeatures:
    """Extract all localization features C1-C5 plus diff stats."""
    overlap = compute_patch_overlap(gold_info, model_info)

    return LocalizationFeatures(
        C1_file_read=_check_file_read(trajectory, gold_info.file_paths),
        C2_func_read=_check_func_read(trajectory, gold_info.func_names),
        C3_file_alignment=_check_file_alignment(model_info, gold_info.file_paths),
        C4_func_alignment=_check_func_alignment(model_info, gold_info.func_names),
        C5_test_executed=_check_test_executed(trajectory),
        diff_hunk_overlap=overlap["hunk_overlap"],
        diff_line_delta=overlap["line_delta"],
        diff_file_delta=overlap["file_delta"],
        modified_test_file=bool(model_info.test_files_modified),
        trajectory_length=len(trajectory.steps),
        max_iterations=trajectory.max_iterations,
        file_overlap_count=overlap["file_overlap_count"],
        func_overlap_count=overlap["func_overlap_count"],
    )
