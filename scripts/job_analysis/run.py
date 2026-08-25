#!/usr/bin/env python3
"""CLI entry point for SWE-bench Analysis Pipeline."""

import argparse
import logging
import sys
from pathlib import Path

from src.config import PipelineConfig, load_config
from src.pipeline import run_pipeline


def apply_cli_overrides(
    cfg: PipelineConfig, args: argparse.Namespace
) -> PipelineConfig:
    """Apply CLI flag overrides onto a loaded PipelineConfig."""
    if args.judge:
        cfg.judge.enabled = True

    if args.include_resolved:
        cfg.analysis.include_resolved = True

    if getattr(args, "task_analysis", False):
        cfg.task_analysis.enabled = True
    if args.no_task_analysis:
        cfg.task_analysis.enabled = False

    if args.traj_analysis:
        cfg.traj_analysis.enabled = True
    if args.no_traj_analysis:
        cfg.traj_analysis.enabled = False

    if args.instance_analysis:
        cfg.instance_analysis.enabled = True
    if args.no_instance_analysis:
        cfg.instance_analysis.enabled = False

    if args.skip_main_pipeline:
        cfg.skip_main_pipeline = True

    return cfg


def effective_config_summary(cfg: PipelineConfig) -> str:
    """One-line summary of post-override pipeline flags."""
    return (
        f"include_resolved={cfg.analysis.include_resolved} "
        f"judge={cfg.judge.enabled} "
        f"task_analysis={cfg.task_analysis.enabled} "
        f"instance_analysis={cfg.instance_analysis.enabled} "
        f"traj_analysis={cfg.traj_analysis.enabled} "
        f"skip_main_pipeline={cfg.skip_main_pipeline}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="SWE-bench Analysis Pipeline (failed + resolved with comparison)"
    )
    parser.add_argument(
        "--config",
        "-c",
        default="configs/default.yaml",
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Enable LLM judge (overrides config)",
    )
    parser.add_argument(
        "--include-resolved",
        action="store_true",
        help="Also analyze resolved instances (overrides config)",
    )
    parser.add_argument(
        "--task-analysis",
        action="store_true",
        help="Enable task-level analysis (difficulty/domain/bug type)",
    )
    parser.add_argument(
        "--no-task-analysis",
        action="store_true",
        help="Disable task-level analysis (overrides config)",
    )
    parser.add_argument(
        "--traj-analysis",
        action="store_true",
        help="Enable trajectory scoring (overrides config)",
    )
    parser.add_argument(
        "--no-traj-analysis",
        action="store_true",
        help="Disable trajectory scoring (overrides config)",
    )
    parser.add_argument(
        "--instance-analysis",
        action="store_true",
        help="Enable instance metadata contingency + correlation analysis (overrides config)",
    )
    parser.add_argument(
        "--no-instance-analysis",
        action="store_true",
        help="Disable instance metadata contingency + correlation analysis (overrides config)",
    )
    parser.add_argument(
        "--skip-main-pipeline",
        action="store_true",
        help="Skip stages 1-6 (feature extraction, labeling, reports); run only optional analysis stages",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("run")

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    cfg = load_config(str(config_path))
    cfg = apply_cli_overrides(cfg, args)

    logger.info("Pipeline config loaded from %s", config_path)
    logger.info("Log dir: %s", cfg.data.log_dir)
    logger.info("Output dir: %s", cfg.output.dir)
    logger.info("Effective config: %s", effective_config_summary(cfg))

    run_pipeline(cfg)


if __name__ == "__main__":
    main()
