from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_DIR = REPO_ROOT / "adapters" / "swerebenchv2"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


utils = _load_module("utils", ADAPTER_DIR / "utils.py")
grader = _load_module("swerebenchv2_grader", ADAPTER_DIR / "template" / "grader.py")


def test_test_commands_restore_test_files_and_preserve_exit_status() -> None:
    script = utils.get_test_commands(
        {
            "instance_id": "owner__repo-1",
            "repo": "owner/repo",
            "base_commit": "abc123",
            "test_patch": (
                "diff --git a/tests/test_a.py b/tests/test_a.py\n"
                "--- a/tests/test_a.py\n"
                "+++ b/tests/test_a.py\n"
            ),
            "install_config": {"test_cmd": "go test ./..."},
        }
    )

    assert "git checkout abc123 -- tests/test_a.py" in script
    assert "git reset" not in script
    assert script.count("restore_test_files") == 4
    assert "patch --batch --forward --fuzz=5" in script
    assert "go test ./..." in script
    assert "TEST_EXIT_CODE=${PIPESTATUS[0]}" in script
    assert "export TEST_PATCH_APPLIED=1" in script
    assert "|| true" not in script


def test_normalize_test_name_handles_timing_and_volatile_snapshot() -> None:
    name = "TestProfileDiff --manifests /tmp/data-snapshot-3409361494/manifests (1.25s)"

    assert grader.normalize_test_name(name) == (
        "TestProfileDiff --manifests /tmp/data-snapshot-<id>/manifests"
    )


def test_rust_test_commands_install_missing_stable_rustfmt() -> None:
    script = utils.get_test_commands(
        {
            "instance_id": "owner__rust-repo-1",
            "repo": "owner/rust-repo",
            "base_commit": "abc123",
            "language": "rust",
            "test_patch": "",
            "install_config": {"test_cmd": "cargo test --workspace"},
        }
    )

    assert "rustup run stable rustfmt --version" in script
    assert "rustup component add rustfmt\n" in script
    assert "rustup component add rustfmt --toolchain stable" in script
    assert "rustup component add rust-src" in script
    assert "export CI=1" in script
    assert "RUN_SLOW_TESTS" not in script


def test_normalize_test_name_handles_dynamic_dates_and_times() -> None:
    expected = (
        "Suite.FromNow(date: 2026-02-16T18:56:21.7937485+00:00, "
        "day: 02/16/2026, defaultValue: 18:56)"
    )
    actual = (
        "Suite.FromNow(date: 2026-08-10T16:03:26.6209789+00:00, "
        "day: 08/10/2026, defaultValue: 16:03)"
    )

    assert grader.normalize_test_name(expected) == grader.normalize_test_name(actual)


def test_swift_parser_recovers_concatenated_results() -> None:
    log = (
        "Test Case 'Suite.first' passed (0.1 seconds)"
        "Test Case 'Suite.second' failed (0.2 seconds)"
    )

    parsed = grader.parse_test_log("parse_log_swift", lambda _: {}, log)

    assert parsed == {"Suite.first": "PASSED", "Suite.second": "FAILED"}


@pytest.mark.parametrize(
    "statuses",
    [
        ["FAILED", "PASSED"],
        ["PASSED", "FAILED"],
    ],
)
def test_parser_preserves_failure_on_normalized_name_collision(
    statuses: list[str],
) -> None:
    names = [
        "Suite.FromNow(date: 2026-02-16T18:56:21+00:00)",
        "Suite.FromNow(date: 2026-08-10T16:03:26+00:00)",
    ]
    parsed = grader.parse_test_log(
        "parse_log_custom",
        lambda _: dict(zip(names, statuses, strict=True)),
        "",
    )

    assert parsed == {"Suite.FromNow(date: <datetime>)": "FAILED"}


def test_infrastructure_error_requires_failed_command() -> None:
    log = "Could not transfer artifact: Network is unreachable"

    assert grader.classify_infrastructure_failure(log, 1) == "network_error"
    assert grader.classify_infrastructure_failure(log, 0) is None


def test_command_not_found_is_a_test_failure_not_infrastructure() -> None:
    log = "/repo/build.sh: line 4: badcommand: command not found"

    assert grader.classify_infrastructure_failure(log, 1) is None


def test_parser_exception_is_a_retryable_grader_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    log_path = tmp_path / "test.log"
    verifier_logs = tmp_path / "verifier"
    config_path.write_text(
        json.dumps(
            {
                "instance_id": "owner__repo-1",
                "install_config": {"log_parser": "exploding_parser"},
            }
        )
    )
    log_path.write_text("malformed test output")

    def exploding_parser(_: str) -> dict[str, str]:
        raise ValueError("parser drift")

    log_parsers = SimpleNamespace(NAME_TO_PARSER={"exploding_parser": exploding_parser})

    exit_code = grader.run_grader(
        log_parsers,
        config_path=config_path,
        log_path=log_path,
        verifier_logs=verifier_logs,
    )

    assert exit_code == 2
    assert "grader failed: parser drift" in capsys.readouterr().err
    assert not (verifier_logs / "report.json").exists()


def test_report_scores_expected_subsets_after_normalization() -> None:
    expected = "TestProfileDiff /tmp/data-snapshot-123/manifests"
    actual = "TestProfileDiff /tmp/data-snapshot-456/manifests"
    report, resolved, infrastructure_failure = grader.build_report(
        {
            "instance_id": "istio__istio-1",
            "FAIL_TO_PASS": [expected],
            "PASS_TO_PASS": [],
        },
        {grader.normalize_test_name(actual): "PASSED"},
        log_content="PASS",
        test_exit_code=0,
        test_patch_applied=True,
    )

    assert resolved is True
    assert infrastructure_failure is None
    assert report["failure_kind"] is None
    assert report["patch_successfully_applied"] is True


def test_difficulty_accepts_dict_llm_metadata() -> None:
    difficulty = utils.get_difficulty(
        {
            "meta": {"llm_metadata": {"difficulty": "medium"}},
        }
    )

    assert difficulty == "medium"
