# Converts SWE-rebench-V2 instances into Harbor task directories
#
# Key differences from the SWE-rebench (V1) adapter:
# - Dataset: ``nebius/SWE-rebench-V2`` with a single ``train`` split (32k tasks).
# - Multi-language: Python, Go, TypeScript, Rust, Java, C, C++, and more.
# - Docker images are pre-built and referenced via the ``image_name`` field.
# - No conda environments — the Docker image handles the full environment.
# - Working directory is ``/{project_name}`` (derived from repo), not ``/testbed``.
# - Grading uses the SWE-rebench-V2 log parsers (cloned into the Docker image)
#   instead of the swebench fork.

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Callable, Iterable, List, Optional, Tuple

from datasets import load_dataset
from utils import (
    get_difficulty,
    get_image_name,
    get_test_commands,
    get_workdir,
    read_text,
    render_literal,
)


@dataclass
class SWERebenchV2Record:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    difficulty: str
    language: str
    patch: Optional[str] = None
    test_patch: Optional[str] = None
    install_config: Optional[dict] = None
    image_name: Optional[str] = None
    pr_description: Optional[str] = None
    interface: Optional[str] = None
    license: Optional[str] = None
    created_at: Optional[int] = None
    FAIL_TO_PASS: Optional[list] = None
    PASS_TO_PASS: Optional[list] = None
    meta: Optional[dict] = None

    @classmethod
    def from_dict(cls, d: dict) -> "SWERebenchV2Record":
        meta = d.get("meta")

        return cls(
            instance_id=d["instance_id"],
            repo=d["repo"],
            base_commit=d["base_commit"],
            problem_statement=d["problem_statement"],
            difficulty=get_difficulty(d),
            language=d.get("language", "unknown"),
            patch=d.get("patch"),
            test_patch=d.get("test_patch"),
            install_config=d.get("install_config"),
            image_name=d.get("image_name"),
            pr_description=d.get("pr_description"),
            interface=d.get("interface"),
            license=d.get("license"),
            created_at=d.get("created_at"),
            FAIL_TO_PASS=d.get("FAIL_TO_PASS"),
            PASS_TO_PASS=d.get("PASS_TO_PASS"),
            meta=meta,
        )


class SWERebenchV2Loader:
    """Cache the SWE-rebench-V2 train split for fast lookup."""

    def __init__(self) -> None:
        ds = load_dataset("nebius/SWE-rebench-V2")["train"]
        self._by_id = {ex["instance_id"]: ex for ex in ds}

    def all_ids(self) -> List[str]:
        return list(self._by_id.keys())

    def load(self, instance_id: str) -> SWERebenchV2Record:
        if instance_id not in self._by_id:
            raise KeyError(f"Instance not found: {instance_id}")
        return SWERebenchV2Record.from_dict(self._by_id[instance_id])

    def get_raw(self, instance_id: str) -> dict:
        if instance_id not in self._by_id:
            raise KeyError(f"Instance not found: {instance_id}")
        return self._by_id[instance_id]

    def all_records(self) -> List[dict]:
        return list(self._by_id.values())


class HarborTaskPaths:
    """Convenience paths for writing a Harbor task."""

    def __init__(self, task_dir: Path) -> None:
        self.task_dir = Path(task_dir)
        self.environment_dir = self.task_dir / "environment"
        self.tests_dir = self.task_dir / "tests"
        self.solution_dir = self.task_dir / "solution"

        self.instruction_path = self.task_dir / "instruction.md"
        self.config_path = self.task_dir / "task.toml"

        self.environment_dir.mkdir(parents=True, exist_ok=True)
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        self.solution_dir.mkdir(parents=True, exist_ok=True)

        self.test_sh_path = self.tests_dir / "test.sh"
        self.config_json_path = self.tests_dir / "config.json"
        self.grader_path = self.tests_dir / "grader.py"
        self.dockerfile_path = self.environment_dir / "Dockerfile"
        self.solve_sh_path = self.solution_dir / "solve.sh"


class SWERebenchV2ToHarbor:
    """
    SWE-rebench-V2 -> Harbor converter using file templates from ./template

    Produces:
      task_dir/
        instruction.md
        task.toml
        environment/
          Dockerfile
        tests/
          test.sh
          config.json
        solution/
          solve.sh
    """

    def __init__(
        self,
        harbor_tasks_root: Path,
        max_timeout_sec: float = 3000.0,
        template_dir: Optional[Path] = None,
        cpus: int = 2,
        memory_mb: int = 8192,
        storage_mb: int = 20480,
    ) -> None:
        self.out_root = Path(harbor_tasks_root)
        self.out_root.mkdir(parents=True, exist_ok=True)

        self.template_dir = Path(template_dir or (Path(__file__).parent / "template"))

        # Resolve template paths
        self.t_instruction = self.template_dir / "instruction.md"
        self.t_config = self.template_dir / "task.toml"
        self.t_test_sh = self.template_dir / "test.sh"
        self.t_grader = self.template_dir / "grader.py"
        self.t_dockerfile = self.template_dir / "Dockerfile"
        self.t_solve = self.template_dir / "solve.sh"

        # Load dataset
        self.loader = SWERebenchV2Loader()

        self.max_timeout = float(max_timeout_sec)
        self.cpus = int(cpus)
        self.memory_mb = int(memory_mb)
        self.storage_mb = int(storage_mb)

    def get_all_ids(self) -> List[str]:
        return sorted(self.loader.all_ids())

    def get_ids_by_languages(
        self,
        *,
        include_languages: Optional[Iterable[str]] = None,
        exclude_languages: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Return instance_ids filtered by included and excluded languages."""
        include_set = {
            language.lower()
            for language in (include_languages or [])
            if language and language.strip()
        }
        exclude_set = {
            language.lower()
            for language in (exclude_languages or [])
            if language and language.strip()
        }

        return sorted(
            iid
            for iid in self.loader.all_ids()
            if (
                (
                    not include_set
                    or self.loader.get_raw(iid).get("language", "").lower()
                    in include_set
                )
                and self.loader.get_raw(iid).get("language", "").lower()
                not in exclude_set
            )
        )

    def get_ids_by_language(self, language: str) -> List[str]:
        """Return instance_ids filtered by programming language."""
        return self.get_ids_by_languages(include_languages=[language])

    # ------------------------------------------------------------------ #
    # Convert a single task
    # ------------------------------------------------------------------ #
    def generate_task(
        self,
        instance_id: str,
        local_task_id: str,
        *,
        overwrite: bool = False,
        image_name_override: Optional[str] = None,
    ) -> Path:
        rec = self.loader.load(instance_id)
        raw = self.loader.get_raw(instance_id)
        task_dir = self.out_root / local_task_id

        if task_dir.exists():
            if not overwrite:
                raise FileExistsError(f"Target already exists: {task_dir}")
            shutil.rmtree(task_dir)

        paths = HarborTaskPaths(task_dir)

        # -- instruction.md --
        instr_tpl = read_text(self.t_instruction)
        instr = render_literal(
            instr_tpl,
            problem_statement=dedent(rec.problem_statement).strip(),
        )
        if not instr.endswith("\n"):
            instr += "\n"
        paths.instruction_path.write_text(instr)

        # -- task.toml --
        cfg_tpl = read_text(self.t_config)
        cfg = render_literal(
            cfg_tpl,
            difficulty=rec.difficulty or "hard",
            max_timeout=str(int(self.max_timeout)),
            cpus=str(self.cpus),
            memory_mb=str(self.memory_mb),
            storage_mb=str(self.storage_mb),
        )
        paths.config_path.write_text(cfg)

        # -- tests/config.json --
        datum = _serialize_record(raw)
        paths.config_json_path.write_text(json.dumps(datum, indent=2))

        # -- tests/test.sh --
        test_sh_tpl = read_text(self.t_test_sh)
        test_commands = get_test_commands(raw)
        test_sh = render_literal(test_sh_tpl, test_commands=test_commands)
        paths.test_sh_path.write_text(test_sh)
        paths.test_sh_path.chmod(0o755)

        # -- tests/grader.py --
        paths.grader_path.write_text(read_text(self.t_grader))
        paths.grader_path.chmod(0o755)

        # -- environment/Dockerfile --
        docker_image = image_name_override or get_image_name(raw)
        workdir = get_workdir(raw)
        dockerfile_tpl = read_text(self.t_dockerfile)
        dockerfile = render_literal(
            dockerfile_tpl,
            docker_image=docker_image,
            workdir=workdir,
        )
        paths.dockerfile_path.write_text(dockerfile)

        # -- solution/solve.sh --
        solve_tpl = read_text(self.t_solve)
        patch_text = (rec.patch or "").strip()
        solve_sh = render_literal(solve_tpl, patch=patch_text)
        paths.solve_sh_path.write_text(solve_sh)
        paths.solve_sh_path.chmod(0o755)

        return paths.task_dir

    # ------------------------------------------------------------------ #
    # Convert many tasks
    # ------------------------------------------------------------------ #
    def generate_many(
        self,
        instance_ids: Iterable[str],
        *,
        name_fn: Optional[Callable[[str], str]] = None,
        overwrite: bool = False,
        image_name_map: Optional[dict[str, str]] = None,
        max_workers: int = 1,
    ) -> Tuple[List[Path], List[tuple[str, str]]]:
        """
        Convert multiple instances.
        Returns (success_paths, failures[(instance_id, reason), ...])
        """
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")

        indexed_ids = list(enumerate(instance_ids, 1))
        successes_by_index: dict[int, Path] = {}
        failures_by_index: dict[int, tuple[str, str]] = {}
        _img_map = image_name_map or {}

        def generate_one(
            idx: int, iid: str
        ) -> tuple[int, str, Path | None, str | None]:
            local_name = name_fn(iid) if name_fn else iid
            try:
                out = self.generate_task(
                    iid,
                    local_name,
                    overwrite=overwrite,
                    image_name_override=_img_map.get(iid),
                )
                return idx, iid, out, None
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                return idx, iid, None, msg

        if max_workers == 1:
            results = (generate_one(idx, iid) for idx, iid in indexed_ids)
            for idx, iid, out, error in results:
                if error is None and out is not None:
                    print(f"[{idx}] OK   {iid} -> {out}", flush=True)
                    successes_by_index[idx] = out
                else:
                    print(f"[{idx}] FAIL {iid}: {error}", flush=True)
                    failures_by_index[idx] = (iid, error or "Unknown error")
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(generate_one, idx, iid): (idx, iid)
                    for idx, iid in indexed_ids
                }
                for future in as_completed(futures):
                    idx, iid, out, error = future.result()
                    if error is None and out is not None:
                        print(f"[{idx}] OK   {iid} -> {out}", flush=True)
                        successes_by_index[idx] = out
                    else:
                        print(f"[{idx}] FAIL {iid}: {error}", flush=True)
                        failures_by_index[idx] = (iid, error or "Unknown error")

        success = [successes_by_index[idx] for idx in sorted(successes_by_index)]
        failures = [failures_by_index[idx] for idx in sorted(failures_by_index)]
        return success, failures


def _serialize_record(raw: dict) -> dict:
    """
    Ensure the raw HF record is JSON-serializable.
    HF datasets may contain special types (e.g., numpy int64, datasets.Sequence).
    """
    out = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = _serialize_record(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [_serialize_item(i) for i in v]
        elif isinstance(v, (int, float, str, bool, type(None))):
            out[k] = v
        else:
            try:
                out[k] = v.item()
            except (AttributeError, ValueError):
                out[k] = str(v)
    return out


def _serialize_item(item):
    if isinstance(item, dict):
        return _serialize_record(item)
    if isinstance(item, (list, tuple)):
        return [_serialize_item(i) for i in item]
    if isinstance(item, (int, float, str, bool, type(None))):
        return item
    try:
        return item.item()
    except (AttributeError, ValueError):
        return str(item)
