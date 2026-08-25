#!/usr/bin/env python3
"""Tag Harbor dataset tasks with language/area/topic/bug-class metadata."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import requests


AREA_TAGS = {"backend", "frontend", "fullstack", "cli", "library", "framework"}
TAG_COUNT = 4

MAX_INSTRUCTION_CHARS = 4000
MAX_PATCH_CHARS = 12000
MAX_TEST_FILE_CHARS = 2000
MAX_TOTAL_TEST_CHARS = 8000
MAX_CONFIG_FIELD_CHARS = 3000
TAG_COMPLETION_TOKENS = 1024
T = TypeVar("T")

WEIGHTS = {
    "patch_scope": 0.30,
    "logic_complexity": 0.25,
    "context_breadth": 0.20,
    "test_complexity": 0.15,
    "instruction_complexity": 0.10,
}

TAG_SYSTEM_PROMPT = """You generate SWE task tags.

IMPORTANT: You MUST respond with a valid JSON object ONLY. No markdown, no explanation outside JSON.
Do not think step by step. Output the JSON object immediately.

TAGS:
Generate exactly 4 tags in this order:
1. Primary programming language, e.g. "python", "javascript", "typescript", "go", "rust", "java", "ruby", "cpp"
2. Tier/area. Choose ONE from: "backend", "frontend", "fullstack", "cli", "library", "framework"
3. Framework/library name, e.g. "fastapi", "django", "react", "nextjs", "axios", "express"; OR a specific topic, e.g. "http", "async", "testing"
4. Bug class: a domain-independent short label for the defect mechanism, e.g. "missing-fallback", "incomplete-validation", "wrong-default", "type-handling-inconsistency", "missing-metadata-propagation"

The bug class should describe the logical failure mode, not the affected framework.

Examples:
- FastAPI backend bug caused by missing default fallback: ["python", "backend", "fastapi", "missing-fallback"]
- Next.js UI bug caused by incorrect state propagation: ["typescript", "frontend", "nextjs", "missing-state-propagation"]
- Python CLI bug caused by incomplete option parsing: ["python", "cli", "argparse", "incomplete-parsing"]

Return format:
{"tags": ["language", "tier-or-area", "framework-or-topic", "bug-class"]}
"""

BUG_CLASS_SYSTEM_PROMPT = """You generate SWE task bug-class tags.

IMPORTANT: You MUST respond with a valid JSON object ONLY. No markdown, no explanation outside JSON.
Do not think step by step. Output the JSON object immediately.

Generate exactly one bug class: a domain-independent short label for the defect mechanism.
Good examples: "missing-fallback", "incomplete-validation", "wrong-default", "type-handling-inconsistency", "missing-metadata-propagation".

The bug class should describe the logical failure mode, not the programming language, framework, or affected feature area.

Return format:
{"bug_class": "bug-class"}
"""


@dataclass(frozen=True)
class DimensionScore:
    name: str
    raw: float
    score: float
    detail: str


@dataclass(frozen=True)
class TaskScore:
    task_id: str
    score: float
    label: str
    dimensions: list[DimensionScore]
    patch_files: int
    patch_lines: int
    test_files: int
    test_lines: int
    instruction_chars: int
    directories: int


@dataclass(frozen=True)
class TaggerConfig:
    model: str
    api_key: str
    base_url: str
    timeout_sec: float = 120.0
    retries: int = 0
    retry_delay_sec: float = 2.0


@dataclass(frozen=True)
class ProcessResult:
    task_id: str
    status: str
    detail: str = ""


def is_harbor_task_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "instruction.md").is_file()
        and (path / "task.toml").is_file()
        and (path / "tests").is_dir()
        and (
            (path / "solution" / "fix.patch").is_file()
            or (path / "solution" / "solve.sh").is_file()
        )
    )


def is_task_metadata_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "instruction.md").is_file()
        and (path / "task.toml").is_file()
    )


def discover_task_dirs(root: Path, dataset: str | None = None) -> list[Path]:
    root = root.expanduser().resolve()
    if dataset:
        root = root / dataset
    if is_task_metadata_dir(root):
        return [root]
    if not root.is_dir():
        raise FileNotFoundError(f"Input path is not a directory: {root}")

    direct_task_dirs = [path for path in root.iterdir() if is_task_metadata_dir(path)]
    if direct_task_dirs:
        return sorted(direct_task_dirs, key=lambda path: path.name)

    nested_task_dirs: list[Path] = []
    for dataset_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        nested_task_dirs.extend(
            path for path in dataset_dir.iterdir() if is_task_metadata_dir(path)
        )
    if nested_task_dirs:
        return sorted(nested_task_dirs, key=lambda path: str(path.relative_to(root)))

    task_dirs = {
        path.parent
        for path in root.rglob("task.toml")
        if "environment" not in path.parts and is_task_metadata_dir(path.parent)
    }
    return sorted(task_dirs, key=lambda path: str(path.relative_to(root)))


def _read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if limit is not None and len(text) > limit:
        return text[:limit] + "\n... (truncated)"
    return text


def _read_task_toml(task_dir: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(
            (task_dir / "task.toml").read_text(encoding="utf-8", errors="replace")
        )
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_config_json(task_dir: Path) -> dict[str, Any]:
    config_path = task_dir / "tests" / "config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_patch_from_config(task_dir: Path) -> str:
    data = _read_config_json(task_dir)
    patch = data.get("patch")
    return patch if isinstance(patch, str) else ""


def _extract_patch_from_solve_sh(task_dir: Path) -> str:
    text = _read_text(task_dir / "solution" / "solve.sh")
    if not text:
        return ""
    heredoc_pattern = re.compile(
        r"<<-?\s*['\"]?(?P<marker>[A-Za-z_][A-Za-z0-9_]*)['\"]?\n(?P<body>.*?)\n(?P=marker)",
        flags=re.DOTALL,
    )
    for match in heredoc_pattern.finditer(text):
        body = match.group("body")
        if "diff --git " in body:
            return body
    return ""


def load_solution_patch_text(task_dir: Path) -> tuple[str, str]:
    fix_patch = task_dir / "solution" / "fix.patch"
    if fix_patch.exists():
        patch_text = _read_text(fix_patch)
        if patch_text:
            return patch_text, "solution/fix.patch"

    patch_text = _extract_patch_from_config(task_dir)
    if patch_text:
        return patch_text, "tests/config.json:patch"

    patch_text = _extract_patch_from_solve_sh(task_dir)
    if patch_text:
        return patch_text, "solution/solve.sh"

    return "", ""


def _parse_patch_text(patch_text: str) -> dict[str, Any]:
    files = 0
    hunks = 0
    additions = 0
    deletions = 0
    dirs: set[str] = set()
    new_defs = 0
    control_flow = 0
    current_file = ""
    control_flow_re = re.compile(
        r"\b(if|elif|else|for|while|try|except|with|match|case|switch|catch)\b"
    )
    new_def_re = re.compile(r"^\+\s*(def|async\s+def|class|function|const|let|var)\b")

    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            files += 1
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[3].removeprefix("b/")
                parent = str(Path(current_file).parent)
                if parent and parent != ".":
                    dirs.add(parent)
            continue
        if line.startswith("@@"):
            hunks += 1
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
            if new_def_re.search(line):
                new_defs += 1
            if control_flow_re.search(line):
                control_flow += 1
        elif line.startswith("-"):
            deletions += 1

    return {
        "files": files,
        "hunks": hunks,
        "additions": additions,
        "deletions": deletions,
        "dirs": dirs,
        "new_defs": new_defs,
        "control_flow": control_flow,
    }


def _count_test_files(task_dir: Path) -> tuple[int, int]:
    test_files = 0
    test_lines = 0
    for path in sorted((task_dir / "tests").rglob("*")):
        if not path.is_file() or path.name == "test.sh":
            continue
        test_files += 1
        text = _read_text(path)
        test_lines += len(text.splitlines())
    return test_files, test_lines


def _scale(raw: float, *, easy: float, hard: float) -> float:
    if raw <= easy:
        return 1.0
    if raw >= hard:
        return 5.0
    ratio = math.log1p(raw - easy) / math.log1p(hard - easy)
    return round(1.0 + ratio * 4.0, 2)


def _score_task(task_dir: Path) -> TaskScore:
    patch_text, _ = load_solution_patch_text(task_dir)
    patch_info = _parse_patch_text(patch_text)
    test_files, test_lines = _count_test_files(task_dir)
    instr_chars = len(_read_text(task_dir / "instruction.md"))

    patch_lines = patch_info["additions"] + patch_info["deletions"]
    patch_files = patch_info["files"]
    patch_hunks = patch_info["hunks"]
    directories = len(patch_info["dirs"])
    dims = [
        DimensionScore(
            "patch_scope",
            patch_lines,
            _scale(patch_lines + patch_files * 8 + patch_hunks * 3, easy=20, hard=260),
            f"{patch_files} files, {patch_lines} changed lines, {patch_hunks} hunks",
        ),
        DimensionScore(
            "logic_complexity",
            patch_info["new_defs"] + patch_info["control_flow"],
            _scale(
                patch_info["new_defs"] * 10
                + patch_info["control_flow"] * 4
                + patch_lines,
                easy=20,
                hard=220,
            ),
            f"{patch_info['new_defs']} defs/classes, {patch_info['control_flow']} control-flow additions",
        ),
        DimensionScore(
            "context_breadth",
            directories,
            _scale(directories * 15 + patch_files * 4 + patch_hunks, easy=15, hard=120),
            f"{directories} directories",
        ),
        DimensionScore(
            "test_complexity",
            test_lines,
            _scale(test_lines + test_files * 10, easy=35, hard=350),
            f"{test_files} test files, {test_lines} test lines",
        ),
        DimensionScore(
            "instruction_complexity",
            instr_chars,
            _scale(instr_chars, easy=1500, hard=12000),
            f"{instr_chars} instruction chars",
        ),
    ]
    weighted_sum = sum(dimension.score * WEIGHTS[dimension.name] for dimension in dims)
    final_score = round(1.0 + (weighted_sum - 1.0) * 2.05, 1)
    final_score = max(1.0, min(10.0, final_score))
    if final_score <= 4.0:
        label = "easy"
    elif final_score <= 7.0:
        label = "medium"
    else:
        label = "hard"
    return TaskScore(
        task_id=task_dir.name,
        score=final_score,
        label=label,
        dimensions=dims,
        patch_files=patch_files,
        patch_lines=patch_lines,
        test_files=test_files,
        test_lines=test_lines,
        instruction_chars=instr_chars,
        directories=directories,
    )


def _patch_changed_files(patch_text: str) -> list[str]:
    files: list[str] = []
    for line in patch_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            files.append(parts[3].removeprefix("b/"))
    return files


def _format_config_field(
    data: dict[str, Any], key: str, limit: int = MAX_CONFIG_FIELD_CHARS
) -> str:
    value = data.get(key)
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    if len(text) > limit:
        text = text[:limit] + "\n... (truncated)"
    return f"{key}:\n{text}"


def _config_context(task_dir: Path) -> str:
    data = _read_config_json(task_dir)
    if not data:
        return ""
    fields = [
        _format_config_field(data, "repo", 500),
        _format_config_field(data, "instance_id", 500),
        _format_config_field(data, "problem_statement"),
        _format_config_field(data, "hints_text"),
        _format_config_field(data, "FAIL_TO_PASS", 1500),
        _format_config_field(data, "PASS_TO_PASS", 1500),
        _format_config_field(data, "test_patch", MAX_TOTAL_TEST_CHARS),
    ]
    return "\n\n".join(field for field in fields if field)


def _test_context(task_dir: Path) -> str:
    entries: list[tuple[Path, str]] = []
    total = 0
    for path in sorted((task_dir / "tests").rglob("*")):
        if not path.is_file() or path.name == "test.sh":
            continue
        content = _read_text(path, MAX_TEST_FILE_CHARS)
        if not content:
            continue
        if total + len(content) > MAX_TOTAL_TEST_CHARS:
            break
        entries.append((path.relative_to(task_dir / "tests"), content))
        total += len(content)
    if not entries:
        return "No test file snippets available."
    return "\n\n".join(f"--- {rel} ---\n{content}" for rel, content in entries)


def build_tag_prompt(task_dir: Path) -> str:
    instruction = _read_text(task_dir / "instruction.md", MAX_INSTRUCTION_CHARS)
    patch_text, patch_source = load_solution_patch_text(task_dir)
    if len(patch_text) > MAX_PATCH_CHARS:
        patch_text = patch_text[:MAX_PATCH_CHARS] + "\n... (truncated)"
    changed_files = _patch_changed_files(patch_text)
    test_context = _config_context(task_dir) or _test_context(task_dir)

    return _sanitize_for_openai(
        "\n".join(
            [
                f"Task name: {task_dir.name}",
                "",
                "Instruction:",
                instruction,
                "",
                f"Changed files from {patch_source or 'solution patch'}:",
                "\n".join(f"- {path}" for path in changed_files) or "- none detected",
                "",
                "Patch excerpt:",
                patch_text,
                "",
                "Task/test metadata:",
                test_context,
                "",
                'Generate the 4 tags for this task using the required order. Return only: {"tags": [...]}',
            ]
        )
    )


def build_bug_class_prompt(task_dir: Path, existing_tags: list[str]) -> str:
    instruction = _read_text(task_dir / "instruction.md", MAX_INSTRUCTION_CHARS)
    patch_text, patch_source = load_solution_patch_text(task_dir)
    if len(patch_text) > MAX_PATCH_CHARS:
        patch_text = patch_text[:MAX_PATCH_CHARS] + "\n... (truncated)"
    changed_files = _patch_changed_files(patch_text)
    test_context = _config_context(task_dir) or _test_context(task_dir)

    return _sanitize_for_openai(
        "\n".join(
            [
                f"Task name: {task_dir.name}",
                f"Existing semantic tags: {', '.join(existing_tags)}",
                "",
                "Instruction:",
                instruction,
                "",
                f"Changed files from {patch_source or 'solution patch'}:",
                "\n".join(f"- {path}" for path in changed_files) or "- none detected",
                "",
                "Patch excerpt:",
                patch_text,
                "",
                "Task/test metadata:",
                test_context,
                "",
                'Generate only the bug class for this task. Return only: {"bug_class": "..."}',
            ]
        )
    )


def _sanitize_for_openai(text: str) -> str:
    return text.replace("\x00", "")


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json(content: str) -> str:
    stripped = _strip_code_fence(content)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("LLM response did not contain a JSON object")
    return stripped[start : end + 1]


def _normalize_tag(value: Any) -> str:
    if value is None:
        return ""
    tag = str(value).strip().lower()
    tag = re.sub(r"\s+", "-", tag)
    tag = re.sub(r"[^a-z0-9_.+-]+", "-", tag)
    tag = re.sub(r"-{2,}", "-", tag).strip("-")
    return tag


def normalize_tags(raw_tags: Any) -> list[str]:
    if not isinstance(raw_tags, list):
        raise RuntimeError("LLM response field 'tags' is not a list")
    if any(tag is None for tag in raw_tags):
        raise RuntimeError("LLM response field 'tags' contains null")
    tags = [_normalize_tag(tag) for tag in raw_tags]
    tags = [tag for tag in tags if tag]
    if len(tags) < TAG_COUNT:
        raise RuntimeError(f"LLM generated only {len(tags)} tags")
    tags = tags[:TAG_COUNT]
    if tags[1] not in AREA_TAGS:
        raise RuntimeError(f"LLM generated invalid area tag: {tags[1]}")
    return tags


def read_task_toml_tags(task_dir: Path) -> list[str]:
    data = _read_task_toml(task_dir)
    metadata = data.get("metadata") if isinstance(data, dict) else None
    if not isinstance(metadata, dict):
        return []
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        return []
    return [_normalize_tag(tag) for tag in tags if _normalize_tag(tag)]


def has_current_tag_schema(tags: list[str]) -> bool:
    return len(tags) == TAG_COUNT and tags[1] in AREA_TAGS and all(tags)


def has_three_part_tag_schema(tags: list[str]) -> bool:
    return len(tags) == TAG_COUNT - 1 and tags[1] in AREA_TAGS and all(tags)


def _chat_completion_content(
    *,
    config: TaggerConfig,
    system_prompt: str,
    user_prompt: str,
) -> str:
    base_url = config.base_url.rstrip("/")
    if not base_url:
        raise RuntimeError("OpenAI-compatible base URL is empty")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [
                {
                    "role": "system",
                    "content": _sanitize_for_openai(system_prompt),
                },
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": TAG_COMPLETION_TOKENS,
        },
        timeout=config.timeout_sec,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM response did not contain choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned empty content")
    return content


def _with_retries(config: TaggerConfig, operation: Callable[[], T]) -> T:
    attempts = max(1, config.retries + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(config.retry_delay_sec * attempt)
    if last_error is None:
        raise RuntimeError("LLM operation failed")
    raise last_error


def _generate_tags_once(task_dir: Path, config: TaggerConfig) -> list[str]:
    content = _chat_completion_content(
        config=config,
        system_prompt=TAG_SYSTEM_PROMPT,
        user_prompt=build_tag_prompt(task_dir),
    )
    parsed = json.loads(_extract_json(content))
    if isinstance(parsed, list):
        return normalize_tags(parsed)
    if isinstance(parsed, dict):
        return normalize_tags(parsed.get("tags"))
    raise RuntimeError("LLM response is neither an object nor a tag list")


def generate_tags_for_task(task_dir: Path, config: TaggerConfig) -> list[str]:
    return _with_retries(config, lambda: _generate_tags_once(task_dir, config))


def normalize_bug_class(raw_bug_class: Any) -> str:
    if raw_bug_class is None:
        raise RuntimeError("LLM response field 'bug_class' is missing or null")
    bug_class = _normalize_tag(raw_bug_class)
    if not bug_class:
        raise RuntimeError("LLM generated an empty bug_class")
    return bug_class


def _generate_bug_class_once(
    task_dir: Path, existing_tags: list[str], config: TaggerConfig
) -> str:
    content = _chat_completion_content(
        config=config,
        system_prompt=BUG_CLASS_SYSTEM_PROMPT,
        user_prompt=build_bug_class_prompt(task_dir, existing_tags),
    )
    parsed = json.loads(_extract_json(content))
    if isinstance(parsed, str):
        return normalize_bug_class(parsed)
    if isinstance(parsed, dict):
        return normalize_bug_class(parsed.get("bug_class"))
    raise RuntimeError("LLM response is neither an object nor a bug_class string")


def generate_bug_class_for_task(
    task_dir: Path, existing_tags: list[str], config: TaggerConfig
) -> str:
    return _with_retries(
        config, lambda: _generate_bug_class_once(task_dir, existing_tags, config)
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _replace_or_insert_metadata_key(
    metadata_text: str, key: str, value_text: str
) -> str:
    pattern = re.compile(rf"(?m)^({re.escape(key)}\s*=\s*).*$")
    replacement = rf"\g<1>{value_text}"
    if pattern.search(metadata_text):
        return pattern.sub(replacement, metadata_text, count=1)

    lines = metadata_text.splitlines()
    if lines and lines[0].strip() == "[metadata]":
        lines.insert(1, f"{key} = {value_text}")
        return "\n".join(lines) + ("\n" if metadata_text.endswith("\n") else "")
    prefix = "[metadata]\n"
    return prefix + f"{key} = {value_text}\n" + metadata_text


def update_task_toml(
    task_dir: Path,
    tags: list[str],
    difficulty: str | None = None,
    archive_existing_tags_key: str | None = None,
) -> None:
    task_toml = task_dir / "task.toml"
    text = task_toml.read_text(encoding="utf-8")
    data = _read_task_toml(task_dir)
    metadata = data.get("metadata") if isinstance(data, dict) else None
    existing_tags = metadata.get("tags") if isinstance(metadata, dict) else None
    should_archive_tags = (
        archive_existing_tags_key is not None
        and isinstance(metadata, dict)
        and isinstance(existing_tags, list)
        and archive_existing_tags_key not in metadata
    )
    metadata_match = re.search(r"(?ms)^\[metadata\]\n.*?(?=^\[|\Z)", text)
    if metadata_match:
        metadata_text = metadata_match.group(0)
        updated = metadata_text
        if should_archive_tags:
            updated = _replace_or_insert_metadata_key(
                updated,
                archive_existing_tags_key,
                _toml_array([str(tag) for tag in existing_tags]),
            )
        if difficulty is not None:
            updated = _replace_or_insert_metadata_key(
                updated, "difficulty", _toml_string(difficulty)
            )
        updated = _replace_or_insert_metadata_key(updated, "tags", _toml_array(tags))
        new_text = (
            text[: metadata_match.start()] + updated + text[metadata_match.end() :]
        )
    else:
        archive_text = ""
        if should_archive_tags:
            archive_text = (
                f"{archive_existing_tags_key} = "
                f"{_toml_array([str(tag) for tag in existing_tags])}\n"
            )
        difficulty_text = ""
        if difficulty is not None:
            difficulty_text = f"difficulty = {_toml_string(difficulty)}\n"
        metadata_text = (
            f"[metadata]\n{archive_text}{difficulty_text}tags = {_toml_array(tags)}\n\n"
        )
        new_text = metadata_text + text

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=task_toml.parent,
        prefix=f".{task_toml.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_name = handle.name
        handle.write(new_text)
    os.replace(tmp_name, task_toml)


def process_task(
    task_dir: Path,
    *,
    config: TaggerConfig,
    force: bool = False,
    dry_run: bool = False,
    archive_existing_tags_key: str | None = None,
) -> ProcessResult:
    existing_tags = read_task_toml_tags(task_dir)
    if existing_tags and has_current_tag_schema(existing_tags) and not force:
        return ProcessResult(task_dir.name, "skipped", "current tags")
    if existing_tags and has_three_part_tag_schema(existing_tags) and not force:
        if dry_run:
            return ProcessResult(task_dir.name, "would_update", "missing bug_class")

        bug_class = generate_bug_class_for_task(task_dir, existing_tags, config)
        tags = [*existing_tags, bug_class]
        update_task_toml(
            task_dir, tags, archive_existing_tags_key=archive_existing_tags_key
        )
        return ProcessResult(task_dir.name, "updated", f"bug_class: {bug_class}")

    reason = "force"
    if not force:
        reason = "invalid tags" if existing_tags else "missing or legacy tags"

    if dry_run:
        return ProcessResult(task_dir.name, "would_update", reason)

    tags = generate_tags_for_task(task_dir, config)
    difficulty = _score_task(task_dir).label
    update_task_toml(
        task_dir,
        tags,
        difficulty,
        archive_existing_tags_key=archive_existing_tags_key,
    )
    return ProcessResult(task_dir.name, "updated", f"{reason}; {difficulty}: {tags}")


def process_tasks(
    task_dirs: list[Path],
    *,
    config: TaggerConfig,
    jobs: int = 1,
    max_tasks: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    archive_existing_tags_key: str | None = None,
    progress_callback: Callable[[ProcessResult], None] | None = None,
) -> list[ProcessResult]:
    if max_tasks is not None:
        task_dirs = task_dirs[: max(0, max_tasks)]
    max_workers = max(1, int(jobs))
    results: list[ProcessResult] = []

    def run_one(task_dir: Path) -> ProcessResult:
        try:
            return process_task(
                task_dir,
                config=config,
                force=force,
                dry_run=dry_run,
                archive_existing_tags_key=archive_existing_tags_key,
            )
        except Exception as exc:
            return ProcessResult(task_dir.name, "error", str(exc))

    if max_workers == 1 or len(task_dirs) <= 1:
        for task_dir in task_dirs:
            result = run_one(task_dir)
            results.append(result)
            if progress_callback:
                progress_callback(result)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_one, task_dir): task_dir for task_dir in task_dirs
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if progress_callback:
                progress_callback(result)
    return results


def resolve_config(args: argparse.Namespace) -> TaggerConfig:
    model = args.model or os.environ.get("OPENAI_MODEL")
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    base_url = (
        args.base_url
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    if args.dry_run:
        return TaggerConfig(
            model=model or "dry-run",
            api_key=api_key or "dry-run",
            base_url=base_url,
            timeout_sec=args.timeout_sec,
            retries=max(0, args.retries),
            retry_delay_sec=max(0.0, args.retry_delay_sec),
        )
    if not model:
        raise RuntimeError("Provide --model or set OPENAI_MODEL")
    if not api_key:
        raise RuntimeError("Provide --api-key or set OPENAI_API_KEY")
    return TaggerConfig(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_sec=args.timeout_sec,
        retries=max(0, args.retries),
        retry_delay_sec=max(0.0, args.retry_delay_sec),
    )


def _print_progress(result: ProcessResult) -> None:
    detail = f" ({result.detail})" if result.detail else ""
    print(f"[{result.status}] {result.task_id}{detail}", flush=True)


def summarize_results(results: list[ProcessResult]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets-root", type=Path, default=Path("datasets"))
    parser.add_argument("--dataset", help="Only process one dataset subdirectory")
    parser.add_argument("--model", help="OpenAI-compatible model name")
    parser.add_argument("--api-key", help="OpenAI-compatible API key")
    parser.add_argument(
        "--base-url", help="OpenAI-compatible base URL, e.g. http://host:port/v1"
    )
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="Retry failed LLM calls this many times before marking the task as error",
    )
    parser.add_argument(
        "--retry-delay-sec",
        type=float,
        default=2.0,
        help="Base retry backoff delay in seconds",
    )
    parser.add_argument("--jobs", "-j", type=int, default=1)
    parser.add_argument(
        "--max",
        dest="max_tasks",
        type=int,
        help="Maximum number of discovered tasks to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report actions; do not call LLM or write files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate tags even when current four-part tags exist",
    )
    parser.add_argument(
        "--archive-existing-tags-key",
        help=(
            "Before writing new tags, copy existing metadata.tags to this metadata "
            "key if it is not already present, e.g. tags_v0"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = resolve_config(args)
    task_dirs = discover_task_dirs(args.datasets_root, dataset=args.dataset)
    results = process_tasks(
        task_dirs,
        config=config,
        jobs=args.jobs,
        max_tasks=args.max_tasks,
        force=args.force,
        dry_run=args.dry_run,
        archive_existing_tags_key=args.archive_existing_tags_key,
        progress_callback=_print_progress,
    )
    summary = summarize_results(results)
    print("Summary:", ", ".join(f"{key}={summary[key]}" for key in sorted(summary)))
    return 1 if summary.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
