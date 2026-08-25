from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_module() -> ModuleType:
    path = (
        Path(__file__).parents[2] / "scripts" / "task_analysis" / "tag_task_metadata.py"
    )
    spec = importlib.util.spec_from_file_location("tag_task_metadata", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


metadata = _load_module()


def _write_task(
    root: Path,
    tags_line: str | None = None,
    solve_sh: bool = False,
    difficulty: str = "unknown",
) -> Path:
    task_dir = root / "dataset" / "owner__repo-1"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "solution").mkdir()
    (task_dir / "instruction.md").write_text("Fix the missing fallback.\n")
    (task_dir / "tests" / "config.json").write_text(
        '{"repo": "owner/repo", "problem_statement": "Fallback is missing."}\n'
    )
    (task_dir / "tests" / "test_patch.diff").write_text(
        "diff --git a/tests/test_bug.py b/tests/test_bug.py\n"
        "--- a/tests/test_bug.py\n"
        "+++ b/tests/test_bug.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    patch = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1 @@\n"
        "-return None\n"
        "+return default\n"
    )
    if solve_sh:
        (task_dir / "solution" / "solve.sh").write_text(
            f"#!/bin/bash\ncat > /tmp/fix.patch <<'PATCH'\n{patch}PATCH\n"
        )
    else:
        (task_dir / "solution" / "fix.patch").write_text(patch)

    tags_text = f"{tags_line}\n" if tags_line is not None else ""
    (task_dir / "task.toml").write_text(
        "[metadata]\n"
        'author_name = "unknown"\n'
        f'difficulty = "{difficulty}"\n'
        f"{tags_text}"
        "\n"
        "[metadata.extra]\n"
        'instance_id = "owner__repo-1"\n'
    )
    return task_dir


def test_process_task_writes_four_part_tags_and_difficulty(tmp_path, monkeypatch):
    task_dir = _write_task(tmp_path)
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    tags = ["python", "backend", "django", "missing-fallback"]
    monkeypatch.setattr(
        metadata, "generate_tags_for_task", lambda task_dir, config: tags
    )

    result = metadata.process_task(task_dir, config=config)

    assert result.status == "updated"
    text = (task_dir / "task.toml").read_text()
    assert 'difficulty = "easy"' in text
    assert 'tags = ["python", "backend", "django", "missing-fallback"]' in text
    assert "[metadata.extra]" in text


def test_process_task_appends_bug_class_to_three_part_tags(tmp_path, monkeypatch):
    task_dir = _write_task(
        tmp_path,
        'tags = ["python", "backend", "django"]',
        difficulty="hard",
    )
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    monkeypatch.setattr(
        metadata,
        "generate_bug_class_for_task",
        lambda task_dir, existing_tags, config: "missing-fallback",
    )
    monkeypatch.setattr(
        metadata,
        "generate_tags_for_task",
        lambda task_dir, config: (_ for _ in ()).throw(
            AssertionError("should not regenerate all tags")
        ),
    )

    result = metadata.process_task(task_dir, config=config)

    assert result.status == "updated"
    text = (task_dir / "task.toml").read_text()
    assert 'difficulty = "hard"' in text
    assert 'tags = ["python", "backend", "django", "missing-fallback"]' in text


def test_process_task_rewrites_legacy_tags(tmp_path, monkeypatch):
    task_dir = _write_task(
        tmp_path, 'tags = ["debugging", "swe-bench", "openswe", "python"]'
    )
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    tags = ["python", "library", "pytest", "incomplete-validation"]
    monkeypatch.setattr(
        metadata, "generate_tags_for_task", lambda task_dir, config: tags
    )

    result = metadata.process_task(task_dir, config=config)

    assert result.status == "updated"
    text = (task_dir / "task.toml").read_text()
    assert 'tags = ["python", "library", "pytest", "incomplete-validation"]' in text
    assert "swe-bench" not in text


def test_process_task_archives_existing_tags(tmp_path, monkeypatch):
    task_dir = _write_task(
        tmp_path, 'tags = ["debugging", "swe-mirror", "openswe", "python"]'
    )
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    tags = ["python", "library", "pytest", "incomplete-validation"]
    monkeypatch.setattr(
        metadata, "generate_tags_for_task", lambda task_dir, config: tags
    )

    result = metadata.process_task(
        task_dir, config=config, archive_existing_tags_key="tags_v0"
    )

    assert result.status == "updated"
    text = (task_dir / "task.toml").read_text()
    assert 'tags_v0 = ["debugging", "swe-mirror", "openswe", "python"]' in text
    assert 'tags = ["python", "library", "pytest", "incomplete-validation"]' in text


def test_process_task_does_not_overwrite_archived_tags(tmp_path, monkeypatch):
    task_dir = _write_task(
        tmp_path,
        'tags_v0 = ["original"]\n'
        'tags = ["debugging", "swe-mirror", "openswe", "python"]',
    )
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    tags = ["python", "library", "pytest", "incomplete-validation"]
    monkeypatch.setattr(
        metadata, "generate_tags_for_task", lambda task_dir, config: tags
    )

    metadata.process_task(task_dir, config=config, archive_existing_tags_key="tags_v0")

    text = (task_dir / "task.toml").read_text()
    assert 'tags_v0 = ["original"]' in text
    assert "swe-mirror" not in text


def test_discover_task_dirs_includes_minimal_metadata_tasks(tmp_path):
    full_task_dir = _write_task(tmp_path)
    minimal_task_dir = tmp_path / "dataset" / "owner__repo-2"
    minimal_task_dir.mkdir()
    (minimal_task_dir / "instruction.md").write_text("Fix the bug.\n")
    (minimal_task_dir / "task.toml").write_text(
        "[metadata]\n"
        'difficulty = "unknown"\n'
        'tags = ["debugging", "swe-bench", "openswe", "python"]\n'
    )

    assert metadata.discover_task_dirs(tmp_path / "dataset") == [
        full_task_dir,
        minimal_task_dir,
    ]


def test_normalize_tags_rejects_null_tag():
    try:
        metadata.normalize_tags(["python", "backend", "django", None])
    except RuntimeError as exc:
        assert "contains null" in str(exc)
    else:
        raise AssertionError("expected null tag to fail validation")


def test_normalize_bug_class_rejects_null():
    try:
        metadata.normalize_bug_class(None)
    except RuntimeError as exc:
        assert "missing or null" in str(exc)
    else:
        raise AssertionError("expected null bug_class to fail validation")


def test_generate_bug_class_retries_empty_content(tmp_path, monkeypatch):
    task_dir = _write_task(tmp_path, 'tags = ["python", "backend", "django"]')
    config = metadata.TaggerConfig(
        model="m",
        api_key="k",
        base_url="http://example.test/v1",
        retries=1,
        retry_delay_sec=0,
    )
    responses = [
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": '{"bug_class": "missing-fallback"}'}}]},
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(*args, **kwargs):
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(metadata.requests, "post", fake_post)

    assert (
        metadata.generate_bug_class_for_task(
            task_dir, ["python", "backend", "django"], config
        )
        == "missing-fallback"
    )
    assert responses == []


def test_process_task_skips_current_schema_tags(tmp_path, monkeypatch):
    task_dir = _write_task(
        tmp_path, 'tags = ["python", "backend", "django", "missing-fallback"]'
    )
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    monkeypatch.setattr(
        metadata,
        "generate_tags_for_task",
        lambda task_dir, config: (_ for _ in ()).throw(
            AssertionError("should not call LLM")
        ),
    )

    before = (task_dir / "task.toml").read_text()
    result = metadata.process_task(task_dir, config=config)

    assert result.status == "skipped"
    assert (task_dir / "task.toml").read_text() == before


def test_dry_run_does_not_write_or_call_llm(tmp_path, monkeypatch):
    task_dir = _write_task(tmp_path)
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    monkeypatch.setattr(
        metadata,
        "generate_tags_for_task",
        lambda task_dir, config: (_ for _ in ()).throw(
            AssertionError("should not call LLM")
        ),
    )

    before = (task_dir / "task.toml").read_text()
    result = metadata.process_task(task_dir, config=config, dry_run=True)

    assert result.status == "would_update"
    assert (task_dir / "task.toml").read_text() == before


def test_dry_run_three_part_tags_does_not_call_bug_class_llm(tmp_path, monkeypatch):
    task_dir = _write_task(tmp_path, 'tags = ["python", "backend", "django"]')
    config = metadata.TaggerConfig(
        model="m", api_key="k", base_url="http://example.test/v1"
    )
    monkeypatch.setattr(
        metadata,
        "generate_bug_class_for_task",
        lambda task_dir, existing_tags, config: (_ for _ in ()).throw(
            AssertionError("should not call LLM")
        ),
    )

    before = (task_dir / "task.toml").read_text()
    result = metadata.process_task(task_dir, config=config, dry_run=True)

    assert result.status == "would_update"
    assert result.detail == "missing bug_class"
    assert (task_dir / "task.toml").read_text() == before


def test_load_solution_patch_from_solve_sh(tmp_path):
    task_dir = _write_task(tmp_path, solve_sh=True)

    patch_text, source = metadata.load_solution_patch_text(task_dir)

    assert source == "solution/solve.sh"
    assert "diff --git a/src/app.py b/src/app.py" in patch_text
