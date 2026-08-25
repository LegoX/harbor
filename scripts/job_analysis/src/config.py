"""YAML config loader with dataclass-backed configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class DataConfig:
    log_dir: str = ""
    trajectory_layout: str = "openhands_jsonl"  # openhands_jsonl or harbor_job
    trajectory_file: str = "output.critic_attempt_1.jsonl"
    trajectory_subpath: str = "agent/litellm-trajectory.jsonl"
    test_file: str = "test-00000-of-00001.jsonl"
    gold_source: str = "jsonl"  # jsonl or harbor_dataset
    dataset_dir: str = ""
    swebench_file: str = "output.critic_attempt_1.swebench.jsonl"
    report_file: str = "output.critic_attempt_1.report.json"
    errors_file: str = "output_errors.jsonl"
    trial_result_file: str = "result.json"
    trial_report_subpath: str = "verifier/report.json"
    max_iterations_default: int = 0

    @property
    def log_dir_path(self) -> Path:
        return Path(self.log_dir)

    @property
    def trajectory_path(self) -> Path:
        return self.log_dir_path / self.trajectory_file

    @property
    def test_path(self) -> Path:
        return self.log_dir_path / self.test_file

    @property
    def dataset_dir_path(self) -> Path:
        return Path(self.dataset_dir)

    @property
    def swebench_path(self) -> Path:
        return self.log_dir_path / self.swebench_file

    @property
    def report_path(self) -> Path:
        return self.log_dir_path / self.report_file

    @property
    def errors_path(self) -> Path:
        return self.log_dir_path / self.errors_file


@dataclass
class OutputConfig:
    dir: str = "output"
    instances_jsonl: str = "instances.jsonl"
    report_json: str = "report.json"

    @property
    def output_dir(self) -> Path:
        return Path(self.dir)

    @property
    def instances_path(self) -> Path:
        return self.output_dir / self.instances_jsonl

    @property
    def report_path(self) -> Path:
        return self.output_dir / self.report_json


@dataclass
class AnalysisConfig:
    include_resolved: bool = False
    include_errors: bool = True
    include_empty_patch: bool = True


@dataclass
class FeatureConfig:
    loop_threshold: int = 3
    premature_stop_threshold: float = 0.3
    tool_error_storm_threshold: int = 5


@dataclass
class JudgeConfig:
    enabled: bool = False
    model: str = "claude-sonnet-4-6"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.0
    max_trajectory_chars: int = 8000


@dataclass
class HackDetectorConfig:
    enabled: bool = True


@dataclass
class TaskAnalysisConfig:
    enabled: bool = False


@dataclass
class TrajAnalysisConfig:
    enabled: bool = False
    out_subdir: str = "traj_analysis"
    max_instances: Optional[int] = None


@dataclass
class InstanceAnalysisConfig:
    enabled: bool = True
    out_subdir: str = "instance_analysis"


@dataclass
class PipelineConfig:
    data: DataConfig = field(default_factory=DataConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    hack_detector: HackDetectorConfig = field(default_factory=HackDetectorConfig)
    task_analysis: TaskAnalysisConfig = field(default_factory=TaskAnalysisConfig)
    traj_analysis: TrajAnalysisConfig = field(default_factory=TrajAnalysisConfig)
    instance_analysis: InstanceAnalysisConfig = field(
        default_factory=InstanceAnalysisConfig
    )
    skip_main_pipeline: bool = False
    scaffold: str = "openhands"
    model: str = "<model-name>"
    taxonomy_version: str = "v1"
    judge_version: str = "v1"


def _apply_section(
    section_obj: object, values: dict[str, Any], section_name: str
) -> None:
    """Apply YAML values to a config dataclass, warning on unknown keys."""
    unknown: list[str] = []
    for key, value in values.items():
        if hasattr(section_obj, key):
            setattr(section_obj, key, value)
        else:
            unknown.append(key)
    if unknown:
        logger.warning(
            "Unknown %s config keys ignored: %s",
            section_name,
            ", ".join(sorted(unknown)),
        )


def load_config(config_path: str) -> PipelineConfig:
    """Load YAML config and return PipelineConfig."""
    with open(config_path, "r") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    cfg = PipelineConfig()

    if "data" in raw:
        _apply_section(cfg.data, raw["data"], "data")

    if "output" in raw:
        _apply_section(cfg.output, raw["output"], "output")

    if "analysis" in raw:
        _apply_section(cfg.analysis, raw["analysis"], "analysis")

    if "features" in raw:
        _apply_section(cfg.features, raw["features"], "features")

    if "judge" in raw:
        _apply_section(cfg.judge, raw["judge"], "judge")

    if "hack_detector" in raw:
        _apply_section(cfg.hack_detector, raw["hack_detector"], "hack_detector")

    if "task_analysis" in raw:
        _apply_section(cfg.task_analysis, raw["task_analysis"], "task_analysis")

    if "traj_analysis" in raw:
        _apply_section(cfg.traj_analysis, raw["traj_analysis"], "traj_analysis")

    if "instance_analysis" in raw:
        _apply_section(
            cfg.instance_analysis, raw["instance_analysis"], "instance_analysis"
        )

    top_level_keys = {
        "scaffold",
        "model",
        "taxonomy_version",
        "judge_version",
        "skip_main_pipeline",
    }
    unknown_top = [
        k
        for k in raw
        if k not in top_level_keys
        and k
        not in {
            "data",
            "output",
            "analysis",
            "features",
            "judge",
            "hack_detector",
            "task_analysis",
            "traj_analysis",
            "instance_analysis",
        }
    ]
    if unknown_top:
        logger.warning(
            "Unknown top-level config keys ignored: %s",
            ", ".join(sorted(unknown_top)),
        )

    for k in (
        "scaffold",
        "model",
        "taxonomy_version",
        "judge_version",
        "skip_main_pipeline",
    ):
        if k in raw:
            setattr(cfg, k, raw[k])

    # Resolve log_dir relative to config file location
    if cfg.data.log_dir and not Path(cfg.data.log_dir).is_absolute():
        config_dir = Path(config_path).parent.parent
        cfg.data.log_dir = str(config_dir / cfg.data.log_dir)

    # Resolve dataset dir
    if cfg.data.dataset_dir and not Path(cfg.data.dataset_dir).is_absolute():
        config_dir = Path(config_path).parent.parent
        cfg.data.dataset_dir = str(config_dir / cfg.data.dataset_dir)

    # Resolve output dir — auto-derive from log_dir basename if not set in YAML
    if "output" not in raw or "dir" not in raw.get("output", {}):
        if cfg.data.log_dir:
            cfg.output.dir = str(Path("output") / Path(cfg.data.log_dir).name)
    if cfg.output.dir and not Path(cfg.output.dir).is_absolute():
        config_dir = Path(config_path).parent.parent
        cfg.output.dir = str(config_dir / cfg.output.dir)

    # Allow env var override for judge API key
    if not cfg.judge.api_key:
        cfg.judge.api_key = os.environ.get("ANTHROPIC_API_KEY")

    return cfg
