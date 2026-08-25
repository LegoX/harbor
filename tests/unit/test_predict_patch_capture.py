"""Tests for the predict_patch capture step in Trial."""

import tempfile
from pathlib import Path

import pytest

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    PredictPatchConfig,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.trial.trial import Trial


class _QuickAgent(BaseAgent):
    """Agent that completes immediately."""

    @staticmethod
    def name() -> str:
        return "predict-patch-quick"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        pass


class _RecordingEnvironment(BaseEnvironment):
    """Environment that records every exec() invocation."""

    exec_calls: list[dict[str, object]]
    fail_exec: bool

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exec_calls = []
        self.fail_exec = False

    @staticmethod
    def type() -> EnvironmentType:
        return EnvironmentType.DOCKER

    @property
    def is_mounted(self) -> bool:
        return True

    @property
    def supports_gpus(self) -> bool:
        return False

    @property
    def can_disable_internet(self) -> bool:
        return False

    def _validate_definition(self):
        pass

    async def start(self, force_build: bool) -> None:
        pass

    async def stop(self, delete: bool):
        pass

    async def upload_file(self, source_path, target_path):
        pass

    async def upload_dir(self, source_dir, target_dir):
        pass

    async def download_file(self, source_path, target_path):
        pass

    async def download_dir(self, source_dir, target_dir):
        pass

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.exec_calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "user": user,
                "timeout_sec": timeout_sec,
            }
        )
        if self.fail_exec:
            raise RuntimeError("simulated exec failure")
        if "__HARBOR_PREDICT_PATCH_BASELINE__" in command:
            return ExecResult(
                stdout=(
                    "__HARBOR_PREDICT_PATCH_BASELINE__/testbed\t0123456789abcdef\n"
                ),
                return_code=0,
            )
        return ExecResult(stdout="", return_code=0)


def _create_task_dir(root: Path) -> Path:
    task_dir = root / "predict-patch-task"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        "[agent]\ntimeout_sec = 10.0\n[verifier]\ntimeout_sec = 10.0\n[environment]\n"
    )
    (task_dir / "instruction.md").write_text("Do nothing.")
    env_dir = task_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.sh").write_text(
        "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"
    )
    return task_dir


_CREATED_TRIALS: list[Trial] = []
_CREATED_TMPDIRS: list[tempfile.TemporaryDirectory] = []


@pytest.fixture(autouse=True)
def _close_trial_log_handlers():
    """Close per-trial log file handlers and remove tempdirs.

    Required on Windows where ``tempfile.TemporaryDirectory`` cleanup fails
    while the trial's ``trial.log`` FileHandler is still open.
    """
    yield
    while _CREATED_TRIALS:
        trial = _CREATED_TRIALS.pop()
        try:
            trial._close_logger_handler()
        except Exception:
            pass
    while _CREATED_TMPDIRS:
        td = _CREATED_TMPDIRS.pop()
        try:
            td.cleanup()
        except Exception:
            pass


async def _make_trial(
    tmp_path: Path | None = None,
    *,
    predict_patch: PredictPatchConfig | None = None,
) -> tuple[Trial, _RecordingEnvironment]:
    if tmp_path is None:
        td = tempfile.TemporaryDirectory()
        _CREATED_TMPDIRS.append(td)
        tmp_path = Path(td.name)

    task_dir = _create_task_dir(tmp_path)
    trials_dir = tmp_path / "trials"
    trials_dir.mkdir()

    config_kwargs: dict[str, object] = {
        "task": TaskConfig(path=task_dir),
        "trials_dir": trials_dir,
        "agent": AgentConfig(
            import_path="tests.unit.test_predict_patch_capture:_QuickAgent"
        ),
        "environment": EnvironmentConfig(
            import_path=("tests.unit.test_predict_patch_capture:_RecordingEnvironment"),
            delete=False,
        ),
        "verifier": VerifierConfig(disable=True),
    }
    if predict_patch is not None:
        config_kwargs["predict_patch"] = predict_patch
    config = TrialConfig(**config_kwargs)  # type: ignore[arg-type]
    trial = await Trial.create(config)
    _CREATED_TRIALS.append(trial)
    env = trial._environment
    assert isinstance(env, _RecordingEnvironment)
    return trial, env


class TestPredictPatchConfigDefaults:
    def test_defaults(self):
        cfg = PredictPatchConfig()
        assert cfg.enabled is True
        assert cfg.output_filename == "predict_patch.diff"
        assert "/testbed" in cfg.repo_paths
        assert "/app/src" in cfg.repo_paths
        assert cfg.auto_discover is True
        assert cfg.auto_discover_max_depth == 2
        assert cfg.init_baseline is True
        assert cfg.baseline_tag == "harbor-baseline"
        assert cfg.init_timeout_sec == 120
        assert cfg.capture_timeout_sec == 60

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            ".",
            "..",
            "../escape.diff",
            "subdir/file.diff",
            "/abs/path.diff",
            "back\\slash.diff",
        ],
    )
    def test_output_filename_rejects_non_basename(self, bad: str):
        with pytest.raises(ValueError):
            PredictPatchConfig(output_filename=bad)

    def test_output_filename_allows_hidden_basename(self):
        cfg = PredictPatchConfig(output_filename=".hidden.diff")
        assert cfg.output_filename == ".hidden.diff"

    @pytest.mark.parametrize("bad", [0, -1, -120])
    def test_init_timeout_must_be_positive(self, bad: int):
        with pytest.raises(ValueError):
            PredictPatchConfig(init_timeout_sec=bad)

    @pytest.mark.parametrize("bad", [0, -1, -120])
    def test_capture_timeout_must_be_positive(self, bad: int):
        with pytest.raises(ValueError):
            PredictPatchConfig(capture_timeout_sec=bad)


@pytest.mark.asyncio
class TestCapturePredictPatch:
    async def test_default_enabled_runs_exec_with_expected_script(self):
        trial, env = await _make_trial()
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        call = env.exec_calls[0]
        command = call["command"]
        assert isinstance(command, str)
        assert 'git -C "$d" add -A' in command
        assert 'git -C "$d" diff --cached "$ref"' in command
        assert "/logs/artifacts/predict_patch.diff" in command
        assert "/testbed" in command
        assert "/app/src" in command
        assert call["user"] == "root"

    async def test_disabled_skips_exec(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(enabled=False),
        )
        await trial._capture_predict_patch()
        assert env.exec_calls == []

    async def test_empty_repo_paths_and_no_auto_discover_skips_exec(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(repo_paths=[], auto_discover=False),
        )
        await trial._capture_predict_patch()
        assert env.exec_calls == []

    async def test_exec_failure_is_best_effort(self):
        trial, env = await _make_trial()
        env.fail_exec = True
        await trial._capture_predict_patch()
        assert len(env.exec_calls) == 1

    async def test_custom_output_filename_is_used(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(output_filename="my_diff.patch"),
        )
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "/logs/artifacts/my_diff.patch" in command

    async def test_custom_repo_paths_are_quoted_and_present(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(
                repo_paths=["/weird path/with spaces", "/repo"]
            ),
        )
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "'/weird path/with spaces'" in command
        assert "/repo" in command

    async def test_auto_discover_present_in_default_script(self):
        trial, env = await _make_trial()
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "find /" in command
        assert "-name .git" in command
        assert "-maxdepth 2" in command
        assert "auto-discovered" in command
        for excluded in (
            "'/proc/*'",
            "'/sys/*'",
            "'/dev/*'",
            "'/logs/*'",
            "'/tests/*'",
            "'/solution/*'",
            "'/installed-agent/*'",
            "'/opt/*'",
        ):
            assert excluded in command, f"missing exclusion {excluded}"

    async def test_auto_discover_disabled_absent_from_script(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(auto_discover=False),
        )
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "find /" not in command
        assert "auto-discovered" not in command
        assert 'git -C "$d" diff --cached "$ref"' in command

    async def test_only_auto_discover_when_repo_paths_empty(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(repo_paths=[], auto_discover=True),
        )
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "find /" in command
        assert "for d in" not in command

    async def test_custom_max_depth_is_respected(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(auto_discover_max_depth=4),
        )
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "-maxdepth 4" in command

    async def test_capture_prefers_baseline_tag(self):
        trial, env = await _make_trial()
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "rev-parse --verify --quiet harbor-baseline" in command
        assert "ref=harbor-baseline" in command
        assert "ref=HEAD" in command
        assert 'diff --cached "$ref"' in command

    async def test_capture_uses_custom_baseline_tag(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(baseline_tag="my custom tag"),
        )
        await trial._capture_predict_patch()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "'my custom tag'" in command


@pytest.mark.asyncio
class TestInitPredictPatchBaseline:
    async def test_default_runs_with_expected_script(self):
        trial, env = await _make_trial()
        await trial._init_predict_patch_baseline()

        assert len(env.exec_calls) == 1
        call = env.exec_calls[0]
        command = call["command"]
        assert isinstance(command, str)
        assert "git init -q -b main" in command
        assert "git add -A" in command
        assert "harbor predict_patch baseline" in command
        assert "git tag harbor-baseline" in command
        assert "rev-parse --is-inside-work-tree" in command
        assert "rev-parse HEAD" in command
        assert "/testbed" in command
        assert "/app/src" in command
        assert call["user"] == "root"
        assert trial._predict_patch_repo_path == "/testbed"
        assert trial._predict_patch_baseline_sha == "0123456789abcdef"

    async def test_init_disabled_still_records_existing_repo_without_initializing(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(init_baseline=False),
        )
        await trial._init_predict_patch_baseline()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "if false &&" in command
        assert trial._predict_patch_baseline_sha == "0123456789abcdef"

    async def test_empty_repo_paths_skips_exec(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(repo_paths=[]),
        )
        await trial._init_predict_patch_baseline()
        assert env.exec_calls == []

    async def test_predict_patch_disabled_skips_exec(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(enabled=False),
        )
        await trial._init_predict_patch_baseline()
        assert env.exec_calls == []

    async def test_exec_failure_is_best_effort(self):
        trial, env = await _make_trial()
        env.fail_exec = True
        await trial._init_predict_patch_baseline()
        assert len(env.exec_calls) == 1

    async def test_custom_baseline_tag_is_quoted(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(baseline_tag="release v1.0"),
        )
        await trial._init_predict_patch_baseline()

        assert len(env.exec_calls) == 1
        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        assert "git tag 'release v1.0'" in command

    async def test_init_stops_after_first_success(self):
        trial, env = await _make_trial()
        await trial._init_predict_patch_baseline()

        command = env.exec_calls[0]["command"]
        assert isinstance(command, str)
        marker_idx = command.find("__HARBOR_PREDICT_PATCH_BASELINE__")
        assert marker_idx >= 0
        assert "exit 0;" in command[marker_idx:]

    async def test_capture_uses_sha_saved_before_agent_execution(self):
        trial, env = await _make_trial()
        await trial._init_predict_patch_baseline()
        await trial._capture_predict_patch()

        command = env.exec_calls[-1]["command"]
        assert isinstance(command, str)
        assert "ref=0123456789abcdef" in command
        assert "rev-parse --verify --quiet harbor-baseline" not in command

    async def test_init_passes_configured_timeout(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(init_timeout_sec=37),
        )
        await trial._init_predict_patch_baseline()
        assert env.exec_calls[0]["timeout_sec"] == 37


@pytest.mark.asyncio
class TestCaptureGuardAndTimeout:
    async def test_capture_passes_configured_timeout(self):
        trial, env = await _make_trial(
            predict_patch=PredictPatchConfig(capture_timeout_sec=17),
        )
        await trial._capture_predict_patch()
        assert env.exec_calls[0]["timeout_sec"] == 17

    async def test_capture_is_idempotent_after_success(self):
        trial, env = await _make_trial()
        await trial._capture_predict_patch()
        await trial._capture_predict_patch()
        await trial._capture_predict_patch()
        assert len(env.exec_calls) == 1
        assert trial._predict_patch_captured is True

    async def test_capture_retries_after_exec_failure(self):
        trial, env = await _make_trial()
        env.fail_exec = True
        await trial._capture_predict_patch()
        assert trial._predict_patch_captured is False
        assert len(env.exec_calls) == 1

        env.fail_exec = False
        await trial._capture_predict_patch()
        assert trial._predict_patch_captured is True
        assert len(env.exec_calls) == 2


@pytest.mark.asyncio
class TestCapturePredictPatchInRun:
    async def test_capture_runs_in_full_trial_flow(self):
        trial, env = await _make_trial()
        await trial.run()

        patch_calls = [
            c
            for c in env.exec_calls
            if isinstance(c["command"], str) and "predict_patch.diff" in c["command"]
        ]
        assert len(patch_calls) == 1
