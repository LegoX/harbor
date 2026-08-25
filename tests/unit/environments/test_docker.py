"""Unit tests for DockerEnvironment command construction."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harbor.environments.base import ExecResult
from harbor.environments.docker.docker import DockerEnvironment
from harbor.environments.docker import COMPOSE_NO_NETWORK_PATH
from harbor.models.task.config import EnvironmentConfig, NetworkMode, NetworkPolicy
from harbor.models.trial.paths import TrialPaths


@pytest.fixture
def docker_env(temp_dir):
    """Create a DockerEnvironment with a minimal valid setup."""
    env_dir = temp_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")

    trial_dir = temp_dir / "trial"
    trial_dir.mkdir()
    trial_paths = TrialPaths(trial_dir=trial_dir)
    trial_paths.mkdir()

    return DockerEnvironment(
        environment_dir=env_dir,
        environment_name="test-task",
        session_id="test-task__abc123",
        trial_paths=trial_paths,
        task_env_config=EnvironmentConfig(docker_image="ubuntu:22.04"),
    )


@pytest.fixture
def docker_env_with_persistent_env(temp_dir):
    """Create a DockerEnvironment with persistent env vars."""
    env_dir = temp_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")

    trial_dir = temp_dir / "trial"
    trial_dir.mkdir()
    trial_paths = TrialPaths(trial_dir=trial_dir)
    trial_paths.mkdir()

    return DockerEnvironment(
        environment_dir=env_dir,
        environment_name="test-task",
        session_id="test-task__abc123",
        trial_paths=trial_paths,
        task_env_config=EnvironmentConfig(docker_image="ubuntu:22.04"),
        persistent_env={"FOO": "bar", "BAZ": "qux"},
    )


class TestMergeEnv:
    """Tests for _merge_env behavior."""

    def test_both_empty_returns_none(self, docker_env):
        assert docker_env._merge_env(None) is None

    def test_persistent_only(self, docker_env_with_persistent_env):
        result = docker_env_with_persistent_env._merge_env(None)
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_per_exec_only(self, docker_env):
        result = docker_env._merge_env({"KEY": "val"})
        assert result == {"KEY": "val"}

    def test_merged_per_exec_wins(self, docker_env_with_persistent_env):
        result = docker_env_with_persistent_env._merge_env(
            {"FOO": "override", "NEW": "var"}
        )
        assert result == {"FOO": "override", "BAZ": "qux", "NEW": "var"}


class TestExecPersistentEnv:
    """Tests that exec() includes persistent env vars."""

    async def test_exec_includes_persistent_env(self, docker_env_with_persistent_env):
        """exec() should pass persistent env vars to the docker compose command."""
        docker_env_with_persistent_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0, stdout="", stderr="")
        )

        await docker_env_with_persistent_env.exec("echo hello")

        call_args = docker_env_with_persistent_env._run_docker_compose_command.call_args
        cmd = call_args[0][0]
        # Check that the env vars are passed as -e flags
        assert "-e" in cmd
        assert "FOO=bar" in cmd
        assert "BAZ=qux" in cmd

    async def test_exec_per_exec_env_overrides_persistent(
        self, docker_env_with_persistent_env
    ):
        """Per-exec env vars should override persistent env vars on conflict."""
        docker_env_with_persistent_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0, stdout="", stderr="")
        )

        await docker_env_with_persistent_env.exec("echo hello", env={"FOO": "override"})

        call_args = docker_env_with_persistent_env._run_docker_compose_command.call_args
        cmd = call_args[0][0]
        assert "FOO=override" in cmd
        assert "BAZ=qux" in cmd


class TestUploadDir:
    """Tests for the /. suffix fix in upload_dir."""

    async def test_upload_dir_appends_dot_suffix(self, docker_env):
        """upload_dir should append /. to source_dir so docker cp copies contents,
        not the directory itself, avoiding nested directories when target exists."""
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )

        await docker_env.upload_dir("/local/tests", "/tests")

        docker_env._run_docker_compose_command.assert_any_call(
            ["cp", "/local/tests/.", "main:/tests"],
            check=True,
        )

    async def test_upload_dir_with_path_object(self, docker_env):
        """upload_dir should handle Path objects correctly."""
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )

        await docker_env.upload_dir(Path("/local/solution"), "/solution")

        docker_env._run_docker_compose_command.assert_any_call(
            ["cp", str(Path("/local/solution")) + "/.", "main:/solution"],
            check=True,
        )

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only CRLF fix")
    async def test_upload_dir_runs_crlf_fix_on_windows(self, docker_env):
        """On Windows, upload_dir should run sed to fix CRLF line endings."""
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )

        await docker_env.upload_dir("/local/tests", "/tests")

        assert docker_env._run_docker_compose_command.call_count == 2


class TestDownloadDir:
    """Tests for the /. suffix fix in download_dir."""

    async def test_download_dir_appends_dot_suffix(self, docker_env):
        """download_dir should append /. to the container source path."""
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )
        docker_env.exec = AsyncMock(return_value=ExecResult(return_code=0))

        await docker_env.download_dir("/tests", "/local/tests")

        docker_env._run_docker_compose_command.assert_called_once_with(
            ["cp", "main:/tests/.", "/local/tests"],
            check=True,
        )

    async def test_download_dir_with_path_target(self, docker_env):
        """download_dir should handle Path objects for target_dir."""
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )
        docker_env.exec = AsyncMock(return_value=ExecResult(return_code=0))

        await docker_env.download_dir("/logs/agent", Path("/local/agent"))

        docker_env._run_docker_compose_command.assert_called_once_with(
            ["cp", "main:/logs/agent/.", str(Path("/local/agent"))],
            check=True,
        )


class TestChownBeforeDownload:
    """Tests for best-effort chown before docker compose cp."""

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_download_file_runs_chown_before_cp(
        self, _getgid, _getuid, docker_env
    ):
        """download_file should exec chown before running docker compose cp."""
        calls: list[str] = []

        async def track_exec(command, **kwargs):
            calls.append(f"exec:{command}")
            return ExecResult(return_code=0)

        async def track_cp(command, **kwargs):
            calls.append(f"compose:{command}")
            return ExecResult(return_code=0)

        docker_env.exec = AsyncMock(side_effect=track_exec)
        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_cp)

        await docker_env.download_file("/app/result.txt", "/local/result.txt")

        assert len(calls) == 2
        assert calls[0] == "exec:chown 1000:1000 /app/result.txt"
        assert calls[1].startswith("compose:")

    @patch("harbor.environments.docker.docker.os.getuid", create=True, return_value=501)
    @patch("harbor.environments.docker.docker.os.getgid", create=True, return_value=20)
    async def test_download_dir_runs_recursive_chown_before_cp(
        self, _getgid, _getuid, docker_env
    ):
        """download_dir should exec chown -R before running docker compose cp."""
        calls: list[str] = []

        async def track_exec(command, **kwargs):
            calls.append(f"exec:{command}")
            return ExecResult(return_code=0)

        async def track_cp(command, **kwargs):
            calls.append(f"compose:{command}")
            return ExecResult(return_code=0)

        docker_env.exec = AsyncMock(side_effect=track_exec)
        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_cp)

        await docker_env.download_dir("/logs", "/local/logs")

        assert len(calls) == 2
        assert calls[0] == "exec:chown -R 501:20 /logs"
        assert calls[1].startswith("compose:")

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_download_proceeds_when_chown_fails(
        self, _getgid, _getuid, docker_env
    ):
        """Download should still succeed even if chown exec fails."""
        docker_env.exec = AsyncMock(
            return_value=ExecResult(return_code=1, stdout="Operation not permitted")
        )
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )

        await docker_env.download_file("/app/file.txt", "/local/file.txt")

        docker_env._run_docker_compose_command.assert_called_once()

    async def test_chown_is_noop_without_getuid(self, docker_env):
        """_chown_to_host_user should be a no-op when os.getuid is unavailable."""
        docker_env.exec = AsyncMock(return_value=ExecResult(return_code=0))

        # Simulate Windows by making hasattr(os, "getuid") return False
        with patch("harbor.environments.docker.docker.os") as mock_os:
            del mock_os.getuid
            await docker_env._chown_to_host_user("/some/path")

        docker_env.exec.assert_not_called()


class TestMountComposeOverride:
    """Tests for compose overrides generated from mounts_json."""

    def test_write_mounts_compose_file_passthrough_custom_mounts(self, docker_env):
        """mounts_json is passed through to the compose services.main.volumes list."""
        docker_env._mounts_json = [
            {
                "type": "bind",
                "source": "/data",
                "target": "/mnt/data",
                "read_only": True,
                "bind": {"create_host_path": False},
            },
        ]

        path = docker_env._write_mounts_compose_file()
        compose = json.loads(path.read_text())

        assert compose == {
            "services": {
                "main": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/data",
                            "target": "/mnt/data",
                            "read_only": True,
                            "bind": {"create_host_path": False},
                        }
                    ]
                }
            }
        }

    def test_write_mounts_compose_file_preserves_image_mounts(self, docker_env):
        docker_env._mounts_json = [
            {
                "type": "image",
                "source": "docker.io/example/runtime:1.14.0",
                "target": "/opt/custom-agent-runtime/oh-sdk",
                "read_only": True,
                "image": {"subpath": "opt/custom-agent-runtime/oh-sdk"},
            }
        ]

        path = docker_env._write_mounts_compose_file()
        compose = json.loads(path.read_text())

        assert compose == {
            "services": {
                "main": {
                    "volumes": [
                        {
                            "type": "image",
                            "source": "docker.io/example/runtime:1.14.0",
                            "target": "/opt/custom-agent-runtime/oh-sdk",
                            "read_only": True,
                            "image": {"subpath": "opt/custom-agent-runtime/oh-sdk"},
                        }
                    ]
                }
            }
        }


class TestNetworkPolicyCompose:
    def test_legacy_allow_internet_false_uses_no_network_compose(
        self, temp_dir
    ) -> None:
        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")
        trial_paths = TrialPaths(trial_dir=temp_dir / "trial")
        trial_paths.mkdir()

        docker_env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test-task",
            session_id="test-task__abc123",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(
                docker_image="ubuntu:22.04",
                allow_internet=False,
            ),
        )

        assert COMPOSE_NO_NETWORK_PATH in docker_env._docker_compose_paths

    def test_explicit_public_network_mode_overrides_legacy_no_network_compose(
        self, temp_dir
    ) -> None:
        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")
        trial_paths = TrialPaths(trial_dir=temp_dir / "trial")
        trial_paths.mkdir()

        docker_env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test-task",
            session_id="test-task__abc123",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(
                docker_image="ubuntu:22.04",
                allow_internet=False,
                network_mode=NetworkMode.PUBLIC,
            ),
        )

        assert COMPOSE_NO_NETWORK_PATH not in docker_env._docker_compose_paths

    def test_dynamic_network_policy_skips_legacy_no_network_compose(
        self, temp_dir
    ) -> None:
        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")
        trial_paths = TrialPaths(trial_dir=temp_dir / "trial")
        trial_paths.mkdir()

        docker_env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test-task",
            session_id="test-task__abc123",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(
                docker_image="ubuntu:22.04",
                allow_internet=False,
            ),
            has_dynamic_network_policy=True,
        )

        assert COMPOSE_NO_NETWORK_PATH not in docker_env._docker_compose_paths


class TestDockerNetworkPolicy:
    async def test_public_policy_restore_failure_is_reported(self, docker_env) -> None:
        docker_env._exec_in_main_network_namespace = AsyncMock(
            return_value=ExecResult(return_code=1, stderr="iptables failed")
        )

        with pytest.raises(RuntimeError, match="restore public.*iptables failed"):
            await docker_env.apply_network_policy(
                NetworkPolicy(network_mode=NetworkMode.PUBLIC)
            )

    async def test_restricted_policy_has_fail_closed_ipv6_fallback(
        self, docker_env
    ) -> None:
        compose_commands: list[list[str]] = []
        docker_commands: list[list[str]] = []

        async def track_compose(command, **kwargs):
            compose_commands.append(command)
            return ExecResult(return_code=0, stdout="main-container\n")

        async def track_docker(command, **kwargs):
            docker_commands.append(command)
            if command[:2] == ["inspect", "--format"]:
                return ExecResult(return_code=0, stdout="sha256:main-image\n")
            return ExecResult(return_code=0)

        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_compose)
        docker_env._run_docker_command = AsyncMock(side_effect=track_docker)

        await docker_env.apply_network_policy(
            NetworkPolicy(network_mode=NetworkMode.NO_NETWORK)
        )

        assert compose_commands == [["ps", "-q", "main"]]
        assert docker_commands[0] == [
            "inspect",
            "--format",
            "{{.Image}}",
            "main-container",
        ]
        assert docker_commands[1][:9] == [
            "run",
            "--rm",
            "--network",
            "container:main-container",
            "--cap-add",
            "NET_ADMIN",
            "--entrypoint",
            "bash",
            "sha256:main-image",
        ]
        command = docker_commands[1][-1]
        assert "command -v ip6tables" in command
        assert "ip6tables -L OUTPUT" in command
        assert "/proc/sys/net/ipv6/conf/all/disable_ipv6" in command
        assert "/proc/net/if_inet6" in command
        assert 'interface_name" != "lo' in command
        assert "IPv6 egress could not be disabled" in command
        assert "ESTABLISHED,RELATED" not in command
        assert "ip6tables -P OUTPUT DROP" in command


class TestStartStaleContainerCleanup:
    """Tests for the stale container cleanup in start()."""

    async def test_start_runs_down_before_up(self, docker_env):
        """start() should run 'down --remove-orphans' before 'up -d'."""
        calls = []

        async def track_calls(command, **kwargs):
            calls.append(command)
            return ExecResult(return_code=0)

        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_calls)

        await docker_env.start(force_build=False)

        assert calls[:2] == [
            ["down", "--remove-orphans"],
            ["up", "--detach", "--wait"],
        ]

    async def test_start_with_build_runs_down_before_up(self, docker_env):
        """start(force_build=True) should build, then down, then up."""
        calls = []

        async def track_calls(command, **kwargs):
            calls.append(command)
            return ExecResult(return_code=0)

        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_calls)

        await docker_env.start(force_build=True)

        assert calls[:3] == [
            ["build"],
            ["down", "--remove-orphans"],
            ["up", "--detach", "--wait", "--no-build"],
        ]

    async def test_start_proceeds_when_down_fails(self, docker_env):
        """start() should still attempt 'up -d' even if 'down' fails."""
        calls = []

        async def track_calls(command, **kwargs):
            calls.append(command)
            if command == ["down", "--remove-orphans"]:
                raise RuntimeError("No such container")
            return ExecResult(return_code=0)

        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_calls)

        await docker_env.start(force_build=False)

        assert calls[:2] == [
            ["down", "--remove-orphans"],
            ["up", "--detach", "--wait"],
        ]

    async def test_start_propagates_up_failure(self, docker_env):
        """start() should propagate errors from 'up -d'."""

        async def track_calls(command, **kwargs):
            if command == ["up", "--detach", "--wait"]:
                raise RuntimeError("Container creation failed")
            return ExecResult(return_code=0)

        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_calls)

        with pytest.raises(RuntimeError, match="Container creation failed"):
            await docker_env.start(force_build=False)


class TestGlobalBuildConcurrency:
    """Tests for the process-wide Docker build limiter."""

    async def test_limits_builds_across_different_images(self, temp_dir, monkeypatch):
        monkeypatch.setenv("HARBOR_DOCKER_BUILD_CONCURRENCY", "2")
        DockerEnvironment._global_build_semaphore = None
        DockerEnvironment._global_build_semaphore_loop = None
        DockerEnvironment._global_build_semaphore_limit = None

        active_builds = 0
        max_active_builds = 0

        async def track_calls(command, **kwargs):
            nonlocal active_builds, max_active_builds
            if command == ["build"]:
                active_builds += 1
                max_active_builds = max(max_active_builds, active_builds)
                await asyncio.sleep(0.01)
                active_builds -= 1
            return ExecResult(return_code=0)

        environments = []
        for index in range(4):
            environment_dir = temp_dir / f"environment-{index}"
            environment_dir.mkdir()
            (environment_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")

            trial_dir = temp_dir / f"trial-{index}"
            trial_dir.mkdir()
            trial_paths = TrialPaths(trial_dir=trial_dir)
            trial_paths.mkdir()

            environment = DockerEnvironment(
                environment_dir=environment_dir,
                environment_name=f"test-task-{index}",
                session_id=f"test-task-{index}__abc123",
                trial_paths=trial_paths,
                task_env_config=EnvironmentConfig(),
            )
            environment._run_docker_compose_command = AsyncMock(side_effect=track_calls)
            environments.append(environment)

        async with asyncio.TaskGroup() as task_group:
            for environment in environments:
                task_group.create_task(environment.start(force_build=False))

        assert max_active_builds == 2

    async def test_limits_compose_service_builds_with_prebuilt_main(
        self, temp_dir, monkeypatch
    ):
        monkeypatch.setenv("HARBOR_DOCKER_BUILD_CONCURRENCY", "1")
        DockerEnvironment._global_build_semaphore = None
        DockerEnvironment._global_build_semaphore_loop = None
        DockerEnvironment._global_build_semaphore_limit = None

        environment_dir = temp_dir / "environment"
        environment_dir.mkdir()
        (environment_dir / "docker-compose.yaml").write_text(
            "services:\n  helper:\n    build:\n      context: ./helper\n"
        )

        trial_dir = temp_dir / "trial"
        trial_dir.mkdir()
        trial_paths = TrialPaths(trial_dir=trial_dir)
        trial_paths.mkdir()

        environment = DockerEnvironment(
            environment_dir=environment_dir,
            environment_name="test-task",
            session_id="test-task__abc123",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(docker_image="ubuntu:22.04"),
        )
        calls: list[list[str]] = []
        build_was_limited = False

        async def track_calls(command, **kwargs):
            nonlocal build_was_limited
            calls.append(command)
            if command == ["build"]:
                semaphore = environment._get_global_build_semaphore()
                build_was_limited = semaphore is not None and semaphore.locked()
            return ExecResult(return_code=0)

        environment._run_docker_compose_command = AsyncMock(side_effect=track_calls)

        await environment.start(force_build=False)

        assert calls[:3] == [
            ["build"],
            ["down", "--remove-orphans"],
            ["up", "--detach", "--wait", "--no-build"],
        ]
        assert build_was_limited

    async def test_cancelled_build_exits_before_releasing_permit(
        self, docker_env, monkeypatch
    ):
        monkeypatch.setenv("HARBOR_DOCKER_BUILD_CONCURRENCY", "1")
        DockerEnvironment._global_build_semaphore = None
        DockerEnvironment._global_build_semaphore_loop = None
        DockerEnvironment._global_build_semaphore_limit = None

        communicate_started = asyncio.Event()
        process_terminated = asyncio.Event()
        allow_process_exit = asyncio.Event()
        communicate_calls = 0

        async def communicate():
            nonlocal communicate_calls
            communicate_calls += 1
            if communicate_calls == 1:
                communicate_started.set()
                await asyncio.Future()
            await allow_process_exit.wait()
            process.returncode = -15
            return b"", None

        process = MagicMock()
        process.returncode = None
        process.communicate = AsyncMock(side_effect=communicate)
        process.terminate.side_effect = process_terminated.set

        semaphore = docker_env._get_global_build_semaphore()
        assert semaphore is not None

        with patch(
            "harbor.environments.docker.docker.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            build_task = asyncio.create_task(docker_env.start(force_build=True))
            await communicate_started.wait()
            build_task.cancel()
            await process_terminated.wait()

            assert semaphore.locked()
            assert not build_task.done()

            allow_process_exit.set()
            with pytest.raises(asyncio.CancelledError):
                await build_task

        assert communicate_calls == 2
        assert not semaphore.locked()

    @pytest.mark.parametrize("value", ["0", "-1", "invalid"])
    async def test_rejects_invalid_limit(self, docker_env, monkeypatch, value):
        monkeypatch.setenv("HARBOR_DOCKER_BUILD_CONCURRENCY", value)

        with pytest.raises(
            ValueError,
            match="HARBOR_DOCKER_BUILD_CONCURRENCY must be a positive integer",
        ):
            await docker_env.start(force_build=True)


class TestStopChownBindMounts:
    """Tests for best-effort chown of bind-mounted /logs before stop."""

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_stop_runs_chown_before_down(self, _getgid, _getuid, docker_env):
        """stop() should exec chown -R on /logs before docker compose down."""
        calls: list[str] = []

        async def track_exec(command, **kwargs):
            calls.append(f"exec:{command}")
            return ExecResult(return_code=0)

        async def track_compose(command, **kwargs):
            calls.append(f"compose:{command}")
            return ExecResult(return_code=0)

        docker_env.exec = AsyncMock(side_effect=track_exec)
        docker_env._run_docker_compose_command = AsyncMock(side_effect=track_compose)

        await docker_env.stop(delete=False)

        assert calls[0] == "exec:chown -R 1000:1000 /logs"
        assert any("compose:['down']" in c for c in calls[1:])

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_stop_proceeds_when_chown_fails(self, _getgid, _getuid, docker_env):
        """stop() should still run docker compose down even if chown exec fails."""
        docker_env.exec = AsyncMock(
            return_value=ExecResult(return_code=1, stdout="Operation not permitted")
        )
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )

        await docker_env.stop(delete=False)

        docker_env._run_docker_compose_command.assert_called_once_with(["down"])


class TestPrepareLogsForHost:
    """Tests for prepare_logs_for_host() and its use by stop()."""

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_prepare_logs_for_host_runs_chown(self, _getgid, _getuid, docker_env):
        """prepare_logs_for_host() should exec chown -R on /logs."""
        docker_env.exec = AsyncMock(return_value=ExecResult(return_code=0))

        await docker_env.prepare_logs_for_host()

        docker_env.exec.assert_called_once_with("chown -R 1000:1000 /logs", user="root")

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_prepare_logs_for_host_tolerates_failure(
        self, _getgid, _getuid, docker_env
    ):
        """prepare_logs_for_host() should not raise even if chown fails."""
        docker_env.exec = AsyncMock(side_effect=RuntimeError("permission denied"))

        await docker_env.prepare_logs_for_host()  # must not raise

    @patch(
        "harbor.environments.docker.docker.os.getuid", create=True, return_value=1000
    )
    @patch(
        "harbor.environments.docker.docker.os.getgid", create=True, return_value=1000
    )
    async def test_stop_delegates_chown_to_prepare_logs_for_host(
        self, _getgid, _getuid, docker_env
    ):
        """stop() should call prepare_logs_for_host() so the chown happens once."""
        docker_env._run_docker_compose_command = AsyncMock(
            return_value=ExecResult(return_code=0)
        )
        docker_env.prepare_logs_for_host = AsyncMock()

        await docker_env.stop(delete=False)

        docker_env.prepare_logs_for_host.assert_called_once()


class TestIsMultiContainer:
    def test_false_without_compose_file(self, temp_dir):
        """Dockerfile-only task is not compose-based."""
        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")

        trial_dir = temp_dir / "trial"
        trial_dir.mkdir()
        trial_paths = TrialPaths(trial_dir=trial_dir)
        trial_paths.mkdir()

        env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test",
            session_id="test__1",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(docker_image="ubuntu:22.04"),
        )
        assert env._uses_compose is False

    def test_true_with_compose_file(self, temp_dir):
        """Task with docker-compose.yaml is compose-based."""
        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "docker-compose.yaml").write_text("services:\n  main: {}\n")

        trial_dir = temp_dir / "trial"
        trial_dir.mkdir()
        trial_paths = TrialPaths(trial_dir=trial_dir)
        trial_paths.mkdir()

        env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test",
            session_id="test__1",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(docker_image="ubuntu:22.04"),
        )
        assert env._uses_compose is True


class TestTaskEnvInjection:
    def test_dockerfile_only_merges_into_persistent_env(self, temp_dir, monkeypatch):
        """For Dockerfile-only tasks, resolved task env vars go to persistent_env."""
        monkeypatch.setenv("TEST_SECRET", "secret-val")

        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")

        trial_dir = temp_dir / "trial"
        trial_dir.mkdir()
        trial_paths = TrialPaths(trial_dir=trial_dir)
        trial_paths.mkdir()

        env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test",
            session_id="test__1",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(
                docker_image="ubuntu:22.04",
                env={"MY_KEY": "${TEST_SECRET}", "LITERAL": "val"},
            ),
        )
        assert env._persistent_env["MY_KEY"] == "secret-val"
        assert env._persistent_env["LITERAL"] == "val"

    def test_compose_does_not_merge_into_persistent_env(self, temp_dir, monkeypatch):
        """For compose tasks, task env vars stay out of persistent_env."""
        monkeypatch.setenv("TEST_SECRET", "secret-val")

        env_dir = temp_dir / "environment"
        env_dir.mkdir()
        (env_dir / "docker-compose.yaml").write_text("services:\n  main: {}\n")

        trial_dir = temp_dir / "trial"
        trial_dir.mkdir()
        trial_paths = TrialPaths(trial_dir=trial_dir)
        trial_paths.mkdir()

        env = DockerEnvironment(
            environment_dir=env_dir,
            environment_name="test",
            session_id="test__1",
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(
                docker_image="ubuntu:22.04",
                env={"MY_KEY": "${TEST_SECRET}"},
            ),
        )
        assert "MY_KEY" not in env._persistent_env
