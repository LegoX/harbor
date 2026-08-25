"""Convert GAIR/OpenSWE instances into Harbor task directories."""

from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from datasets import load_dataset
from utils import (
    build_eval_body,
    clone_repo_at_commit,
    read_text,
    render_literal,
    rewrite_dockerfile,
    uses_openswe_base,
)

OSS_CONFIG = "openswe_oss"
OTHER_CONFIG = "openswe_other"
CONFIGS = (OSS_CONFIG, OTHER_CONFIG)


@dataclass
class OpenSWERecord:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    dockerfile: str
    eval_script: str
    image_name: str
    version: str
    license_name: str
    patch: Optional[str] = None
    test_patch: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "OpenSWERecord":
        instance_id = d.get("instance_id")
        if not instance_id:
            raise ValueError(f"Record is missing a non-empty 'instance_id': {d!r}")
        repo = (d.get("repo") or "").strip()
        base_commit = (d.get("base_commit") or "").strip()
        if not repo:
            raise ValueError(f"Record '{instance_id}' is missing a non-empty 'repo'.")
        if not base_commit:
            raise ValueError(
                f"Record '{instance_id}' is missing a non-empty 'base_commit'."
            )
        return cls(
            instance_id=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=d.get("problem_statement", "") or "",
            dockerfile=d.get("Dockerfile", "") or "",
            eval_script=d.get("eval_script", "") or "",
            image_name=d.get("image_name", "") or "",
            version=str(d.get("version", "") or ""),
            license_name=d.get("license_name", "") or "",
            patch=d.get("patch"),
            test_patch=d.get("test_patch"),
            raw=dict(d),
        )


class OpenSWELoader:
    """Stream the OpenSWE dataset and yield selected records.

    The dataset is large (multi-GB), so it is streamed rather than fully
    materialized. Records can be filtered by an explicit instance-id set and
    capped with a limit.
    """

    def __init__(self, config: str) -> None:
        if config not in CONFIGS:
            raise ValueError(f"Unknown config '{config}'. Expected one of {CONFIGS}.")
        self.config = config

    def iter_records(
        self,
        *,
        instance_ids: Optional[set[str]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[OpenSWERecord]:
        ds = load_dataset("GAIR/OpenSWE", self.config, split="train", streaming=True)
        count = 0
        for ex in ds:
            if instance_ids is not None and ex["instance_id"] not in instance_ids:
                continue
            yield OpenSWERecord.from_dict(ex)
            count += 1
            # When selecting an explicit id set we want all of them; stop early
            # only when a plain limit is requested.
            if instance_ids is not None and count >= len(instance_ids):
                break
            if limit is not None and count >= limit:
                break


class HarborTaskPaths:
    """Convenience paths for writing a Harbor task."""

    def __init__(self, task_dir: Path, *, with_solution: bool) -> None:
        self.task_dir = Path(task_dir)
        self.environment_dir = self.task_dir / "environment"
        self.tests_dir = self.task_dir / "tests"
        self.solution_dir = self.task_dir / "solution"

        self.instruction_path = self.task_dir / "instruction.md"
        self.config_path = self.task_dir / "task.toml"

        self.environment_dir.mkdir(parents=True, exist_ok=True)
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        if with_solution:
            self.solution_dir.mkdir(parents=True, exist_ok=True)

        self.repo_dir = self.environment_dir / "repo"
        self.dockerfile_path = self.environment_dir / "Dockerfile"
        self.test_sh_path = self.tests_dir / "test.sh"
        self.eval_body_path = self.tests_dir / "eval_body.sh"
        self.test_patch_path = self.tests_dir / "test_patch.diff"
        self.config_json_path = self.tests_dir / "config.json"
        self.solve_sh_path = self.solution_dir / "solve.sh"


class OpenSWEToHarbor:
    """OpenSWE -> Harbor converter using file templates from ./template."""

    def __init__(
        self,
        config: str,
        out_root: Path,
        *,
        max_timeout_sec: float = 3600.0,
        template_dir: Optional[Path] = None,
        github_base: str = "https://github.com",
        clone_repos: bool = True,
    ) -> None:
        self.config = config
        self.out_root = Path(out_root)
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.max_timeout = float(max_timeout_sec)
        self.github_base = github_base
        self.clone_repos = clone_repos

        self.template_dir = Path(template_dir or (Path(__file__).parent / "template"))
        self.t_instruction = self.template_dir / "instruction.md"
        self.t_config = self.template_dir / "task.toml"
        self.t_dockerfile = self.template_dir / "environment" / "Dockerfile"
        self.t_test_sh = self.template_dir / "tests" / "test.sh"
        self.t_solve = self.template_dir / "solution" / "solve.sh"

    def generate_task(
        self, rec: OpenSWERecord, local_task_id: str, *, overwrite: bool = False
    ) -> Path:
        task_dir = self.out_root / local_task_id
        if task_dir.exists():
            if not overwrite:
                raise FileExistsError(f"Target already exists: {task_dir}")
            shutil.rmtree(task_dir)

        has_patch = bool((rec.patch or "").strip())
        paths = HarborTaskPaths(task_dir, with_solution=has_patch)

        # instruction.md
        instr = render_literal(
            read_text(self.t_instruction),
            problem_statement=rec.problem_statement.strip(),
            repo=rec.repo,
            base_commit=rec.base_commit,
        )
        if not instr.endswith("\n"):
            instr += "\n"
        paths.instruction_path.write_text(instr)

        # task.toml
        cfg = render_literal(
            read_text(self.t_config),
            difficulty="unknown",
            repo=rec.repo,
            base_commit=rec.base_commit,
            instance_id=rec.instance_id,
            openswe_config=self.config,
            max_timeout=str(int(self.max_timeout)),
        )
        paths.config_path.write_text(cfg)

        # environment/Dockerfile (+ repo build context)
        if not rec.dockerfile.strip():
            raise ValueError("Instance has an empty Dockerfile.")
        if not uses_openswe_base(rec.dockerfile):
            raise ValueError(
                "Dockerfile does not inherit from an openswe-python-* base image; "
                "the conda 'testbed' environment cannot be guaranteed."
            )
        dockerfile_body = rewrite_dockerfile(rec.dockerfile)
        dockerfile = render_literal(
            read_text(self.t_dockerfile), dockerfile_body=dockerfile_body
        )
        paths.dockerfile_path.write_text(dockerfile)

        if self.clone_repos:
            clone_repo_at_commit(
                rec.repo,
                rec.base_commit,
                paths.repo_dir,
                github_base=self.github_base,
            )

        # tests/test.sh (static), eval_body.sh (transformed), test_patch.diff
        paths.test_sh_path.write_text(read_text(self.t_test_sh))
        paths.test_sh_path.chmod(0o755)

        eval_body = build_eval_body(
            rec.eval_script, strip_all_patches=(self.config == OSS_CONFIG)
        )
        paths.eval_body_path.write_text(eval_body)
        paths.eval_body_path.chmod(0o755)

        test_patch = (rec.test_patch or "").strip()
        if test_patch and not test_patch.endswith("\n"):
            test_patch += "\n"
        paths.test_patch_path.write_text(test_patch)

        # tests/config.json (lean metadata for debugging)
        meta = {
            "instance_id": rec.instance_id,
            "repo": rec.repo,
            "base_commit": rec.base_commit,
            "version": rec.version,
            "image_name": rec.image_name,
            "license_name": rec.license_name,
            "openswe_config": self.config,
        }
        paths.config_json_path.write_text(json.dumps(meta, indent=2))

        # solution/solve.sh (only when a gold patch is available)
        if has_patch:
            patch_text = (rec.patch or "").strip()
            solve_sh = render_literal(read_text(self.t_solve), patch=patch_text)
            paths.solve_sh_path.write_text(solve_sh)
            paths.solve_sh_path.chmod(0o755)

        return paths.task_dir

    def generate_from_records(
        self,
        records: Iterable[OpenSWERecord],
        *,
        overwrite: bool = False,
        workers: int = 1,
    ) -> tuple[list[Path], list[tuple[str, str]]]:
        if workers <= 1:
            return self._generate_sequential(records, overwrite=overwrite)
        return self._generate_parallel(records, overwrite=overwrite, workers=workers)

    def _generate_sequential(
        self,
        records: Iterable[OpenSWERecord],
        *,
        overwrite: bool,
    ) -> tuple[list[Path], list[tuple[str, str]]]:
        success: list[Path] = []
        failures: list[tuple[str, str]] = []

        for idx, rec in enumerate(records, 1):
            try:
                out = self.generate_task(rec, rec.instance_id, overwrite=overwrite)
                print(f"[{idx}] OK   {rec.instance_id} -> {out}", flush=True)
                success.append(out)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                print(f"[{idx}] FAIL {rec.instance_id}: {msg}", flush=True)
                failures.append((rec.instance_id, msg))
                continue

        return success, failures

    def _generate_parallel(
        self,
        records: Iterable[OpenSWERecord],
        *,
        overwrite: bool,
        workers: int,
    ) -> tuple[list[Path], list[tuple[str, str]]]:
        """Generate tasks concurrently.

        Task generation is dominated by per-instance ``git`` network I/O, which
        releases the GIL, so a thread pool gives a near-linear speedup. The
        dataset stream is consumed by this (single) thread and work is submitted
        with a bounded backlog to cap memory.
        """
        success: list[Path] = []
        failures: list[tuple[str, str]] = []
        lock = threading.Lock()
        counter = {"n": 0}

        def work(rec: OpenSWERecord) -> tuple[str, object]:
            try:
                out = self.generate_task(rec, rec.instance_id, overwrite=overwrite)
                with lock:
                    counter["n"] += 1
                    idx = counter["n"]
                    print(f"[{idx}] OK   {rec.instance_id} -> {out}", flush=True)
                return ("ok", out)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                with lock:
                    counter["n"] += 1
                    idx = counter["n"]
                    print(f"[{idx}] FAIL {rec.instance_id}: {msg}", flush=True)
                return ("fail", (rec.instance_id, msg))

        def drain(done_futures) -> None:
            for fut in done_futures:
                kind, val = fut.result()
                if kind == "ok":
                    success.append(val)  # type: ignore[arg-type]
                else:
                    failures.append(val)  # type: ignore[arg-type]

        max_backlog = workers * 4
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending: set = set()
            for rec in records:
                pending.add(executor.submit(work, rec))
                if len(pending) >= max_backlog:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    drain(done)
            drain(pending)

        return success, failures
