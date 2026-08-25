#!/usr/bin/env python3
"""Copy registry-downloaded Harbor tasks and add phase-scoped egress controls."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

import toml

from harbor.models.task.id import GitTaskId, LocalTaskId, PackageTaskId
from harbor.registry.client.factory import RegistryClientFactory
from harbor.tasks.client import TaskClient


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_ROOT_REGISTRY_PATH = REPO_ROOT / "registry.json"
DEFAULT_SOURCE_TASK_DIR = REPO_ROOT / "datasets" / "swebench-verified-local"
DEFAULT_TASK_DIR = REPO_ROOT / "datasets" / "swebench-verified-nohack"
DEFAULT_OUTPUT_REGISTRY_PATH = (
    REPO_ROOT
    / "scripts"
    / "git_ignore"
    / "hack_control"
    / "registry.swebench_verified_nohack.json"
)
SOURCE_DATASET_NAME = "swebench-verified"
DATASET_NAME = "swebench-verified-nohack"
DATASET_VERSION = "1.0"
DATASET_DESCRIPTION = (
    "Local SWE-bench Verified dataset with agent egress limited to the "
    "configured LiteLLM endpoint and verifier egress left public."
)
HARDEN_MARKER = "# Harbor nohack network controls."
HARDEN_END_MARKER = "# End Harbor nohack network controls."

TaskId = GitTaskId | LocalTaskId | PackageTaskId


DOCKER_COMPOSE_YAML = """\
services:
  main: {}
"""


DOCKERFILE_BLOCK = """

# Harbor nohack network controls.
RUN if ! command -v iptables >/dev/null 2>&1; then \\
      if command -v apt-get >/dev/null 2>&1; then \\
        apt-get update && \\
        apt-get install -y --no-install-recommends iptables iproute2 && \\
        rm -rf /var/lib/apt/lists/*; \\
      elif command -v apk >/dev/null 2>&1; then \\
        apk add --no-cache iptables iproute2; \\
      else \\
        echo "Unsupported package manager for nohack network controls" >&2; \\
        exit 1; \\
      fi; \\
    fi && \\
    command -v iptables >/dev/null 2>&1
# End Harbor nohack network controls.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download a Harbor dataset from a registry into a clean local source "
            "directory, copy it, and add Docker egress controls."
        )
    )
    parser.add_argument(
        "--root-registry-path",
        type=Path,
        default=DEFAULT_ROOT_REGISTRY_PATH,
        help=f"Input registry path. Default: {DEFAULT_ROOT_REGISTRY_PATH}",
    )
    parser.add_argument(
        "--source-dataset",
        default=SOURCE_DATASET_NAME,
        help=f"Registry dataset to download. Default: {SOURCE_DATASET_NAME}",
    )
    parser.add_argument(
        "--source-task-dir",
        type=Path,
        default=DEFAULT_SOURCE_TASK_DIR,
        help=f"Clean downloaded source task root. Default: {DEFAULT_SOURCE_TASK_DIR}",
    )
    parser.add_argument(
        "--task-dir",
        type=Path,
        default=DEFAULT_TASK_DIR,
        help=f"Hardened output task root. Default: {DEFAULT_TASK_DIR}",
    )
    parser.add_argument(
        "--output-registry-path",
        type=Path,
        default=DEFAULT_OUTPUT_REGISTRY_PATH,
        help=f"Output local registry path. Default: {DEFAULT_OUTPUT_REGISTRY_PATH}",
    )
    parser.add_argument(
        "--output-dataset-name",
        default=DATASET_NAME,
        help=f"Dataset name written to the local registry. Default: {DATASET_NAME}",
    )
    parser.add_argument(
        "--output-dataset-description",
        default=None,
        help=(
            "Dataset description written to the local registry. By default a "
            "description is derived from the source dataset."
        ),
    )
    parser.add_argument(
        "--harden-existing",
        action="store_true",
        help=(
            "Update tasks already present under --task-dir without downloading, "
            "copying, or rewriting the registry."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Download and harden only the first N selected tasks.",
    )
    parser.add_argument(
        "--instance-id",
        action="append",
        default=[],
        help="Download and harden a specific task name. Can be passed multiple times.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite hardened task directories before copying from the clean source.",
    )
    parser.add_argument(
        "--download-overwrite",
        action="store_true",
        help="Force re-download of the clean source tasks.",
    )
    return parser.parse_args()


def task_name(task_id: TaskId) -> str:
    return task_id.get_name()


def select_task_ids(task_ids: list[TaskId], args: argparse.Namespace) -> list[TaskId]:
    if args.instance_id:
        requested = list(dict.fromkeys(args.instance_id))
        by_name = {task_name(task_id): task_id for task_id in task_ids}
        missing = [name for name in requested if name not in by_name]
        if missing:
            raise ValueError(
                "Requested task(s) not found in "
                f"{args.source_dataset}: {', '.join(missing)}"
            )
        selected = [by_name[name] for name in requested]
    else:
        selected = list(task_ids)

    if args.limit is not None:
        selected = selected[: args.limit]

    if not selected:
        raise ValueError("No tasks selected.")
    return selected


async def download_source_tasks(args: argparse.Namespace) -> list[Path]:
    client = RegistryClientFactory.create(registry_path=args.root_registry_path)
    metadata = await client.get_dataset_metadata(args.source_dataset)
    selected = select_task_ids(metadata.task_ids, args)

    print(
        f"Ensuring {len(selected)} clean source task(s) in "
        f"{args.source_task_dir.resolve()}"
    )
    result = await TaskClient().download_tasks(
        task_ids=selected,
        overwrite=args.download_overwrite,
        output_dir=args.source_task_dir.resolve(),
    )
    return result.paths


def copy_task(source_task_dir: Path, output_root: Path, overwrite: bool) -> Path:
    target_task_dir = output_root / source_task_dir.name
    if target_task_dir.exists():
        if overwrite:
            shutil.rmtree(target_task_dir)
        else:
            return target_task_dir

    shutil.copytree(source_task_dir, target_task_dir)
    return target_task_dir


def select_existing_task_dirs(args: argparse.Namespace) -> list[Path]:
    if not args.task_dir.is_dir():
        raise FileNotFoundError(f"Task directory not found: {args.task_dir}")

    task_dirs = sorted(path for path in args.task_dir.iterdir() if path.is_dir())
    if args.instance_id:
        requested = list(dict.fromkeys(args.instance_id))
        by_name = {path.name: path for path in task_dirs}
        missing = [name for name in requested if name not in by_name]
        if missing:
            raise ValueError(
                f"Requested task(s) not found in {args.task_dir}: {', '.join(missing)}"
            )
        task_dirs = [by_name[name] for name in requested]

    if args.limit is not None:
        task_dirs = task_dirs[: args.limit]
    if not task_dirs:
        raise ValueError(f"No task directories found under {args.task_dir}.")
    return task_dirs


def harden_task(task_dir: Path) -> None:
    environment_dir = task_dir / "environment"
    dockerfile_path = environment_dir / "Dockerfile"
    task_toml_path = task_dir / "task.toml"

    if not dockerfile_path.exists():
        raise FileNotFoundError(f"Missing Dockerfile: {dockerfile_path}")
    if not task_toml_path.exists():
        raise FileNotFoundError(f"Missing task.toml: {task_toml_path}")

    (environment_dir / "docker-compose.yaml").write_text(DOCKER_COMPOSE_YAML)

    dockerfile = dockerfile_path.read_text()
    if HARDEN_MARKER in dockerfile:
        # The generated block is always appended at EOF. Replacing from its
        # marker upgrades previously hardened tasks without duplicating layers.
        dockerfile = dockerfile.split(HARDEN_MARKER, maxsplit=1)[0]
    hardened_dockerfile = dockerfile.rstrip() + "\n" + DOCKERFILE_BLOCK
    if dockerfile_path.read_text() != hardened_dockerfile:
        dockerfile_path.write_text(hardened_dockerfile)

    task_config = toml.loads(task_toml_path.read_text())
    task_config.setdefault("environment", {})["network_mode"] = "public"
    agent_config = task_config.setdefault("agent", {})
    agent_config["network_mode"] = "allowlist"
    agent_config.setdefault("allowed_hosts", [])
    task_config.setdefault("verifier", {})["network_mode"] = "public"
    task_toml_path.write_text(toml.dumps(task_config))


def write_registry(
    registry_path: Path,
    task_dirs: list[Path],
    *,
    dataset_name: str = DATASET_NAME,
    description: str = DATASET_DESCRIPTION,
) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tasks = []
    for task_dir in sorted(task_dirs, key=lambda path: path.name):
        tasks.append(
            {
                "name": task_dir.name,
                "path": task_dir.resolve().relative_to(REPO_ROOT).as_posix(),
            }
        )

    registry = [
        {
            "name": dataset_name,
            "version": DATASET_VERSION,
            "description": description,
            "tasks": tasks,
        }
    ]
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")


async def async_main() -> None:
    args = parse_args()
    args.root_registry_path = args.root_registry_path.resolve()
    args.source_task_dir = args.source_task_dir.resolve()
    args.task_dir = args.task_dir.resolve()
    args.output_registry_path = args.output_registry_path.resolve()

    if args.harden_existing:
        task_dirs = select_existing_task_dirs(args)
        for task_dir in task_dirs:
            harden_task(task_dir)
        print(f"Updated {len(task_dirs)} hardened task(s) under: {args.task_dir}")
        return

    source_task_dirs = await download_source_tasks(args)
    args.task_dir.mkdir(parents=True, exist_ok=True)

    hardened_task_dirs = []
    for source_task_dir in source_task_dirs:
        target_task_dir = copy_task(source_task_dir, args.task_dir, args.overwrite)
        harden_task(target_task_dir)
        hardened_task_dirs.append(target_task_dir)

    description = args.output_dataset_description
    if description is None:
        if (
            args.source_dataset == SOURCE_DATASET_NAME
            and args.output_dataset_name == DATASET_NAME
        ):
            description = DATASET_DESCRIPTION
        else:
            description = (
                f"Local hardened copy of {args.source_dataset} with agent egress "
                "limited to configured allowlist hosts and verifier egress left public."
            )

    write_registry(
        args.output_registry_path,
        hardened_task_dirs,
        dataset_name=args.output_dataset_name,
        description=description,
    )
    print(f"Wrote hardened task root: {args.task_dir}")
    print(f"Wrote local nohack registry: {args.output_registry_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
