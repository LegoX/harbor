from harbor.models.task.task import Task


def create_minimal_task(task_dir):
    env_dir = task_dir / "environment"
    tests_dir = task_dir / "tests"

    env_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (env_dir / "Dockerfile").write_text("FROM alpine:3.19\n")
    (tests_dir / "test.sh").write_text("#!/usr/bin/env sh\nexit 0\n")
    (task_dir / "instruction.md").write_text("Do something simple.\n")
    (task_dir / "task.toml").write_text(
        """
version = "1.0"

[environment]
""".strip()
    )


def test_task_init_with_dot_path(tmp_path, monkeypatch):
    # Create minimal valid task structure in a temporary directory
    task_dir = tmp_path / "my-task"
    create_minimal_task(task_dir)

    # Change working directory to the task directory
    monkeypatch.chdir(task_dir)

    # Initialize Task using relative path '.'
    task = Task(task_dir=".")

    # Assert paths are resolved to absolute and name is correct
    assert task.task_dir == task_dir.resolve()
    assert task.paths.task_dir == task_dir.resolve()
    assert task.name == task_dir.name


def test_task_checksum_handles_self_referential_symlink(tmp_path):
    task_dir = tmp_path / "my-task"
    create_minimal_task(task_dir)

    bad_link = task_dir / "environment" / "invalid"
    bad_link.symlink_to("invalid")

    task = Task(task_dir)

    assert len(task.checksum) == 64
    assert task.checksum == task.checksum
