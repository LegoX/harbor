"""从 gold patch 和 problem_statement 中提取 task 难度特征."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.parser.patch_parser import PatchInfo, parse_patch


@dataclass
class TaskDifficultyFeatures:
    """单个 instance 的 task 难度特征."""

    instance_id: str

    # ---- gold patch 规模 ----
    gold_files_modified: int = 0
    gold_lines_changed: int = 0  # added + removed
    gold_lines_added: int = 0
    gold_lines_removed: int = 0
    gold_hunk_count: int = 0
    gold_is_new_file: bool = False
    gold_is_delete_file: bool = False

    # ---- 跨文件复杂度 ----
    gold_cross_file: bool = False  # 是否修改了 >= 2 个文件
    gold_cross_dir: bool = False  # 修改的文件是否跨 >= 2 个目录

    # ---- test 复杂度 ----
    fail_to_pass_count: int = 0
    pass_to_pass_count: int = 0
    test_patch_exists: bool = False

    # ---- problem statement 特征 ----
    problem_length: int = 0  # 字符数
    problem_has_code_block: bool = False
    problem_has_traceback: bool = False
    problem_has_error_msg: bool = False

    # ---- SWE-bench 原生难度 ----
    swebench_difficulty: str = (
        ""  # "<15 min fix" / "15 min - 1 hour" / "1-4 hours" / ">4 hours"
    )

    # ---- 综合难度等级 (计算得出) ----
    difficulty_tier: str = ""  # easy / medium / hard / very_hard


# ---------------------------------------------------------------------------
# problem_statement 特征提取
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```|`[^`]+`", re.DOTALL)
_TRACEBACK_RE = re.compile(
    r"Traceback\s*\(|most recent call last|^\s*File\s+\"[^\"]+\",\s*line\s+\d+",
    re.MULTILINE | re.IGNORECASE,
)
_ERROR_MSG_RE = re.compile(
    r"\b(Error|Exception|ValueError|TypeError|KeyError|AttributeError|"
    r"ImportError|RuntimeError|AssertionError|NotImplementedError|"
    r"IndexError|NameError|SyntaxError|ZeroDivisionError|"
    r"FileNotFoundError|PermissionError|OSError)\b",
    re.IGNORECASE,
)


def _extract_problem_features(text: str) -> dict:
    """从 problem_statement 中提取文本特征."""
    return {
        "problem_length": len(text),
        "problem_has_code_block": bool(_CODE_BLOCK_RE.search(text)),
        "problem_has_traceback": bool(_TRACEBACK_RE.search(text)),
        "problem_has_error_msg": bool(_ERROR_MSG_RE.search(text)),
    }


# ---------------------------------------------------------------------------
# gold patch 规模特征
# ---------------------------------------------------------------------------


def _extract_patch_features(info: PatchInfo) -> dict:
    """从 PatchInfo 中提取 patch 规模特征."""
    dirs: set[str] = set()
    for p in info.file_paths:
        parts = p.rsplit("/", 1)
        dirs.add(parts[0] if len(parts) > 1 else ".")

    has_new = any(f.is_new_file for f in info.files)
    has_delete = any(f.is_deleted_file for f in info.files)
    total_hunks = sum(len(f.hunks) for f in info.files)

    return {
        "gold_files_modified": len(info.file_paths),
        "gold_lines_changed": info.total_added + info.total_removed,
        "gold_lines_added": info.total_added,
        "gold_lines_removed": info.total_removed,
        "gold_hunk_count": total_hunks,
        "gold_is_new_file": has_new,
        "gold_is_delete_file": has_delete,
        "gold_cross_file": len(info.file_paths) >= 2,
        "gold_cross_dir": len(dirs) >= 2,
    }


# ---------------------------------------------------------------------------
# 综合难度分级
# ---------------------------------------------------------------------------

_SWEBENCH_DIFFICULTY_ORDER = {
    "<15 min fix": 0,
    "15 min - 1 hour": 1,
    "1-4 hours": 2,
    ">4 hours": 3,
}


def _compute_difficulty_tier(
    swebench_difficulty: str,
    gold_files_modified: int,
    gold_lines_changed: int,
    gold_hunk_count: int,
    gold_cross_file: bool,
    gold_cross_dir: bool,
    fail_to_pass_count: int,
) -> str:
    """综合多维度计算难度等级.

    策略: 以 SWE-bench 原生难度为基底, 结合 patch 规模指标进行微调.
    - easy:   原生 <15 min 且 修改 <=1 文件 / <=5 行
    - medium: 原生 <15 min 但 patch 较大, 或原生 15min-1h 且 patch 适中
    - hard:   原生 15min-1h 且 patch 较大, 或原生 1-4h
    - very_hard: 原生 >4h, 或跨目录 + 大量行修改
    """
    base = _SWEBENCH_DIFFICULTY_ORDER.get(swebench_difficulty, 1)

    # patch 规模得分 (0-3)
    scale_score = 0
    if gold_lines_changed > 50 or gold_hunk_count > 10:
        scale_score = 3
    elif gold_lines_changed > 20 or gold_hunk_count > 5:
        scale_score = 2
    elif gold_lines_changed > 5 or gold_hunk_count > 2:
        scale_score = 1

    # 跨文件/跨目录加分
    cross_bonus = 0
    if gold_cross_dir:
        cross_bonus += 2
    elif gold_cross_file:
        cross_bonus += 1

    # test 复杂度加分
    test_bonus = 1 if fail_to_pass_count > 3 else 0

    # 综合得分
    total = base * 2 + scale_score + cross_bonus + test_bonus

    if total <= 2:
        return "easy"
    elif total <= 5:
        return "medium"
    elif total <= 8:
        return "hard"
    else:
        return "very_hard"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def extract_task_features(
    instance_id: str,
    gold_patch_text: str,
    problem_statement: str,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
    test_patch: str,
    swebench_difficulty: str,
) -> TaskDifficultyFeatures:
    """提取单个 instance 的 task 难度特征."""
    gold_info = parse_patch(gold_patch_text)
    patch_feats = _extract_patch_features(gold_info)
    problem_feats = _extract_problem_features(problem_statement)

    feats = TaskDifficultyFeatures(instance_id=instance_id)
    for k, v in patch_feats.items():
        setattr(feats, k, v)
    for k, v in problem_feats.items():
        setattr(feats, k, v)

    feats.fail_to_pass_count = len(fail_to_pass)
    feats.pass_to_pass_count = len(pass_to_pass)
    feats.test_patch_exists = bool(test_patch and test_patch.strip())
    feats.swebench_difficulty = swebench_difficulty

    feats.difficulty_tier = _compute_difficulty_tier(
        swebench_difficulty=feats.swebench_difficulty,
        gold_files_modified=feats.gold_files_modified,
        gold_lines_changed=feats.gold_lines_changed,
        gold_hunk_count=feats.gold_hunk_count,
        gold_cross_file=feats.gold_cross_file,
        gold_cross_dir=feats.gold_cross_dir,
        fail_to_pass_count=feats.fail_to_pass_count,
    )

    return feats
