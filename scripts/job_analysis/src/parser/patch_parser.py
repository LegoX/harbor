"""Parse unified diff patches, extract modified files and functions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Hunk:
    """A single hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # @@ ... @@ context line
    context_func: str  # function/class name from hunk header
    body_lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    """Diff for a single file."""

    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    is_new_file: bool = False
    is_deleted_file: bool = False

    @property
    def path(self) -> str:
        """Return the canonical file path (strip a/ or b/ prefix)."""
        p = self.new_path if self.new_path else self.old_path
        if p.startswith("b/") or p.startswith("a/"):
            return p[2:]
        return p

    @property
    def is_test_file(self) -> bool:
        """Heuristic: is this a test file?"""
        p = self.path.lower()
        return (
            "/test" in p
            or "/tests/" in p
            or p.startswith("test_")
            or "_test.py" in p
            or "conftest.py" in p
        )


@dataclass
class PatchInfo:
    """Parsed information from a complete patch."""

    files: list[FileDiff] = field(default_factory=list)
    file_paths: set[str] = field(default_factory=set)
    func_names: set[str] = field(default_factory=set)
    total_added: int = 0
    total_removed: int = 0
    test_files_modified: list[str] = field(default_factory=list)


# Regex for unified diff hunk header: @@ -old_start,old_count +new_start,new_count @@ optional_func
HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@\s*(.*)?$")

# Regex for diff file header
FILE_HEADER_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$")

# Regex for extract function/class names from hunk context
FUNC_CLASS_RE = re.compile(r"(?:def|class)\s+(\w+)")


def parse_patch(patch_text: str) -> PatchInfo:
    """Parse a unified diff patch into structured PatchInfo."""
    if not patch_text or not patch_text.strip():
        return PatchInfo()

    info = PatchInfo()
    current_file: FileDiff | None = None
    current_hunk_lines: list[str] = []

    lines = patch_text.split("\n")

    for line in lines:
        # New file header
        m = FILE_HEADER_RE.match(line)
        if m:
            if current_file:
                info.files.append(current_file)
            current_file = FileDiff(old_path=m.group(1), new_path=m.group(2))
            info.file_paths.add(current_file.path)
            current_hunk_lines = []
            continue

        if current_file is None:
            continue

        # Detect new/deleted files
        if line.startswith("new file"):
            current_file.is_new_file = True
            continue
        if line.startswith("deleted file"):
            current_file.is_deleted_file = True
            continue

        # Hunk header
        m = HUNK_RE.match(line)
        if m:
            current_hunk_lines = []

            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            context = m.group(5) or ""

            # Extract function/class name from context
            func_name = ""
            func_match = FUNC_CLASS_RE.search(context)
            if func_match:
                func_name = func_match.group(1)
            else:
                # Try to extract from context like "def foo:" or "class Bar:"
                # Some diffs use @@ -x,y +a,b @@ func_name
                parts = context.strip().split(",")
                if parts and parts[0].strip():
                    candidate = parts[0].strip()
                    if re.match(r"^[a-zA-Z_]\w*$", candidate):
                        func_name = candidate

            hunk = Hunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=line,
                context_func=func_name,
            )
            current_file.hunks.append(hunk)
            if func_name:
                info.func_names.add(func_name)
            continue

        # Count added/removed lines
        if line.startswith("+") and not line.startswith("+++"):
            current_file.added_lines += 1
            info.total_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            current_file.removed_lines += 1
            info.total_removed += 1

        # Collect hunk body lines for function extraction
        if current_file.hunks:
            current_hunk_lines.append(line)
            current_file.hunks[-1].body_lines.append(line)

    # Finalize last file
    if current_file:
        info.files.append(current_file)

    # Identify test files
    for f in info.files:
        if f.is_test_file:
            info.test_files_modified.append(f.path)

    # Also try to extract function names from added/changed lines
    for f in info.files:
        _extract_funcs_from_lines(f, info.func_names)

    return info


def _extract_funcs_from_lines(file_diff: FileDiff, func_names: set[str]) -> None:
    """Extract function/class names from diff lines as fallback."""
    for hunk in file_diff.hunks:
        for line in getattr(hunk, "body_lines", []):
            if line.startswith(("+++", "---")):
                continue
            if line[:1] in {"+", " ", "-"}:
                match = FUNC_CLASS_RE.search(line[1:])
                if match:
                    func_names.add(match.group(1))


def compute_patch_overlap(gold: PatchInfo, model: PatchInfo) -> dict:
    """Compute overlap statistics between gold and model patches."""
    gold_files = gold.file_paths
    model_files = model.file_paths
    gold_funcs = gold.func_names
    model_funcs = model.func_names

    # File overlap
    file_intersection = gold_files & model_files
    file_union = gold_files | model_files
    file_jaccard = len(file_intersection) / len(file_union) if file_union else 0.0

    # Function overlap
    func_intersection = gold_funcs & model_funcs
    func_union = gold_funcs | model_funcs
    func_jaccard = len(func_intersection) / len(func_union) if func_union else 0.0

    # Hunk overlap: for overlapping files, compute line-level overlap
    hunk_overlap = _compute_hunk_overlap(gold, model)

    # Line count diff
    line_delta = abs(
        (model.total_added + model.total_removed)
        - (gold.total_added + gold.total_removed)
    )
    file_delta = abs(len(model_files) - len(gold_files))

    return {
        "file_overlap_count": len(file_intersection),
        "file_jaccard": round(file_jaccard, 3),
        "func_overlap_count": len(func_intersection),
        "func_jaccard": round(func_jaccard, 3),
        "hunk_overlap": round(hunk_overlap, 3),
        "line_delta": line_delta,
        "file_delta": file_delta,
        "gold_files": sorted(gold_files),
        "model_files": sorted(model_files),
        "gold_funcs": sorted(gold_funcs),
        "model_funcs": sorted(model_funcs),
        "shared_files": sorted(file_intersection),
        "shared_funcs": sorted(func_intersection),
    }


def _hunks_by_path(files: list[FileDiff]) -> dict[str, list[Hunk]]:
    """Group hunks by canonical file path without dropping duplicate file sections."""
    grouped: dict[str, list[Hunk]] = {}
    for file_diff in files:
        grouped.setdefault(file_diff.path, []).extend(file_diff.hunks)
    return grouped


def _compute_hunk_overlap(gold: PatchInfo, model: PatchInfo) -> float:
    """Compute hunk-level overlap rate between gold and model patches.

    For each shared file, count hunks that overlap (same file, overlapping line ranges).
    Returns ratio of overlapping hunks to total gold hunks.
    """
    if not gold.files:
        return 0.0

    gold_hunks_by_path = _hunks_by_path(gold.files)
    model_hunks_by_path = _hunks_by_path(model.files)

    overlapping = 0
    total_gold_hunks = 0

    for path, gold_hunks in gold_hunks_by_path.items():
        model_hunks = model_hunks_by_path.get(path, [])
        total_gold_hunks += len(gold_hunks)

        for gh in gold_hunks:
            for mh in model_hunks:
                # Check if hunk line ranges overlap
                g_start = gh.new_start
                g_end = gh.new_start + max(gh.new_count, 1)
                m_start = mh.new_start
                m_end = mh.new_start + max(mh.new_count, 1)

                if g_start <= m_end and m_start <= g_end:
                    overlapping += 1
                    break

    return overlapping / total_gold_hunks if total_gold_hunks else 0.0
