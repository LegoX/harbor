"""Parse gold/test data from SWE-bench JSONL or Harbor dataset configs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GoldInstance:
    """Gold information for a single SWE-bench instance."""

    instance_id: str
    repo: str
    base_commit: str
    patch: str  # gold patch (unified diff)
    test_patch: str  # test patch (unified diff)
    problem_statement: str
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    version: str = ""
    difficulty: str = ""


def _parse_test_list(raw) -> list[str]:
    """Parse JSON-encoded test list string."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def load_gold_instances(test_path: Path) -> dict[str, GoldInstance]:
    """Load gold instance data from test JSONL.

    Returns dict mapping instance_id -> GoldInstance.
    """
    instances = {}
    with open(test_path, "r") as f:
        for line in f:
            entry = json.loads(line)
            inst = _gold_instance_from_entry(entry)
            instances[inst.instance_id] = inst
    logger.info("Loaded %d gold instances from %s", len(instances), test_path)
    return instances


def _gold_instance_from_entry(entry: dict) -> GoldInstance:
    """Build a GoldInstance from SWE-bench-compatible metadata."""
    return GoldInstance(
        instance_id=entry["instance_id"],
        repo=entry.get("repo", ""),
        base_commit=entry.get("base_commit", ""),
        patch=entry.get("patch", ""),
        test_patch=entry.get("test_patch", ""),
        problem_statement=entry.get("problem_statement", ""),
        fail_to_pass=_parse_test_list(entry.get("FAIL_TO_PASS", "")),
        pass_to_pass=_parse_test_list(entry.get("PASS_TO_PASS", "")),
        version=entry.get("version", ""),
        difficulty=entry.get("difficulty", ""),
    )


def load_harbor_gold_instances(dataset_dir: Path) -> dict[str, GoldInstance]:
    """Load gold instances from Harbor dataset */tests/config.json files.

    Some SWE-bench-Pro repos (notably NodeBB) keep the original CamelCase in
    ``config.json::instance_id`` even though Harbor lowercases the trial-side
    ``task_name``. To make the trial-side id resolve cleanly, we additionally
    register a lowercase alias whenever the canonical id is not already
    lowercase.
    """
    instances = {}
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Harbor dataset dir not found: {dataset_dir}")

    aliased = 0
    for task_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
        config_path = task_dir / "tests" / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r") as f:
                entry = json.load(f)
            inst = _gold_instance_from_entry(entry)
            instances[inst.instance_id] = inst
            lower = inst.instance_id.lower()
            if lower != inst.instance_id and lower not in instances:
                instances[lower] = inst
                aliased += 1
        except Exception as e:
            logger.warning("Skipping invalid Harbor gold config %s: %s", config_path, e)

    logger.info(
        "Loaded %d Harbor gold instances from %s (with %d lowercase aliases)",
        len({id(v) for v in instances.values()}),
        dataset_dir,
        aliased,
    )
    return instances
