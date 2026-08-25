import argparse

from run import apply_cli_overrides, effective_config_summary
from src.config import PipelineConfig, load_config


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        judge=False,
        include_resolved=False,
        no_task_analysis=False,
        task_analysis=False,
        traj_analysis=False,
        no_traj_analysis=False,
        instance_analysis=False,
        no_instance_analysis=False,
        skip_main_pipeline=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_include_resolved_override():
    cfg = PipelineConfig()
    cfg.analysis.include_resolved = False
    cfg = apply_cli_overrides(cfg, _args(include_resolved=True))
    assert cfg.analysis.include_resolved is True


def test_judge_override():
    cfg = PipelineConfig()
    assert cfg.judge.enabled is False
    cfg = apply_cli_overrides(cfg, _args(judge=True))
    assert cfg.judge.enabled is True


def test_no_task_analysis_override():
    cfg = PipelineConfig()
    cfg.task_analysis.enabled = True
    cfg = apply_cli_overrides(cfg, _args(no_task_analysis=True))
    assert cfg.task_analysis.enabled is False


def test_task_analysis_override():
    cfg = PipelineConfig()
    assert cfg.task_analysis.enabled is False
    cfg = apply_cli_overrides(cfg, _args(task_analysis=True))
    assert cfg.task_analysis.enabled is True


def test_traj_analysis_mutual_override():
    cfg = PipelineConfig()
    cfg.traj_analysis.enabled = False
    cfg = apply_cli_overrides(cfg, _args(traj_analysis=True))
    assert cfg.traj_analysis.enabled is True

    cfg.traj_analysis.enabled = True
    cfg = apply_cli_overrides(cfg, _args(no_traj_analysis=True))
    assert cfg.traj_analysis.enabled is False


def test_instance_analysis_mutual_override():
    cfg = PipelineConfig()
    cfg.instance_analysis.enabled = False
    cfg = apply_cli_overrides(cfg, _args(instance_analysis=True))
    assert cfg.instance_analysis.enabled is True

    cfg.instance_analysis.enabled = True
    cfg = apply_cli_overrides(cfg, _args(no_instance_analysis=True))
    assert cfg.instance_analysis.enabled is False


def test_skip_main_pipeline_override():
    cfg = PipelineConfig()
    cfg = apply_cli_overrides(cfg, _args(skip_main_pipeline=True))
    assert cfg.skip_main_pipeline is True


def test_analysis_config_yaml_loads(tmp_path):
    config_path = tmp_path / "analysis_config.yaml"
    config_path.write_text(
        """
data:
  trajectory_layout: harbor_job
instance_analysis:
  enabled: true
traj_analysis:
  enabled: true
  max_instances: 20
""".lstrip(),
        encoding="utf-8",
    )

    cfg = load_config(str(config_path))
    assert cfg.data.trajectory_layout == "harbor_job"
    assert cfg.instance_analysis.enabled is True
    assert cfg.traj_analysis.enabled is True
    assert cfg.traj_analysis.max_instances == 20


def test_effective_config_summary():
    cfg = PipelineConfig()
    summary = effective_config_summary(cfg)
    assert "judge=False" in summary
    assert "skip_main_pipeline=False" in summary
