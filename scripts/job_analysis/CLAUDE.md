# CLAUDE.md — Coding Agent Context

工程上下文文档，供 AI agent 修改本目录代码时参考。  
**用户操作指南** → [README.md](README.md)  
**配置字段全集** → [configs/README.md](configs/README.md)  
**设计语义** → [design.md](design.md)

## What This Is

Harbor 本地 SWE-bench **评测归因** pipeline：解析 OpenHands / LiteLLM 轨迹 → 重建 patch → 抽取确定性特征 → 可选 LLM judge → 五轴标签 → 聚合报告（failed / resolved / 对比）。

Harbor 扩展：双输入布局、failed/resolved 对比、task/traj/instance 可选分析、LiteLLM file-editor patch 重建。

## Run

```bash
cd scripts/job_analysis
pip install -r requirements-job-analysis.txt
python run.py --config configs/<profile>.yaml
```

| Profile | Layout |
| --- | --- |
| `default.yaml` | `openhands_jsonl` |
| `openhands_jsonl_full.yaml` | `openhands_jsonl` with all optional stages enabled |
| `harbor_sweb_*.yaml` / `harbor_swem_*.yaml` / `harbor_swep_*.yaml` | `harbor_job` |

CLI overrides: `--include-resolved`, `--judge`, `--no-task-analysis`, `--no-traj-analysis`, `--no-instance-analysis`, `--skip-main-pipeline`.

## Architecture

`src/pipeline.py` orchestrates:

1. Load trajectories (`load_trajectories` / `load_harbor_trajectories`)
2. Load gold (`load_gold_instances` / `load_harbor_gold_instances`)
3. Identify failed/resolved from report metadata (`src/report_metadata.py`)
4. Per instance: parse patches → features → judge → label → write reports
5. Optional: task_analysis, traj_analysis, instance_analysis

Instance ID resolution lives in `src/report_metadata.py` (`resolve_instance_id`).

## Key Modules

| Module | Role |
| --- | --- |
| `parser/trajectory_parser.py` | Trajectory normalize + patch reconstruct (`_FileStateTracker`) |
| `parser/patch_parser.py` | Unified diff parse + hunk overlap |
| `parser/test_parser.py` | Gold from JSONL or Harbor dataset |
| `features/localization.py` | C1–C5 (path suffix + word-boundary func match) |
| `features/pathology.py` | Loop, premature stop, tool storm, truncation |
| `hack_detector/reward_hack.py` | H1/H4/H5 (gated by `hack_detector.enabled`) |
| `judge/tier1_judge.py` | LLM or heuristic; resolved-aware prompt |
| `labeler/axis_labeler.py` | Five axes + flags + attribution |
| `aggregator/report.py` | Distributions + failed/resolved comparison |
| `traj_analysis/` | TQS scoring + JSONL I/O; not `swe_data_process` |
| `instance_analysis/` | Metadata contingency + correlations (needs `scipy`) |

## Config Rules

- YAML → dataclasses in `src/config.py`
- Relative paths resolve from `scripts/job_analysis/` (config file's parent's parent)
- `output.dir` auto-derived as `output/<log_dir_basename>` if unset
- Unknown YAML keys log warnings, not errors
- See `configs/README.md` for full field reference

## Labeling

**Axes**: localization, diagnosis, implementation, tool_usage, long_horizon — see `design.md` §9.

**Flags**: `hack`, `environment_noise`, `missing_gold`, `empty_patch`, `error_instance`, `alternative_fix`

**Attribution priority**:

```text
hack > environment > missing_gold > empty_patch > error > localization
> diagnosis > implementation > tool_usage > long_horizon
```

**Verdicts**: V1 correct → V5 no attempt

## Development Rules

- C1–C4 and hunk overlap are **gold-aware offline only** — never online RL rewards
- C5, tool errors, pathology flags are gold-free — safe for online signals
- Preserve patch reconstruction for view/str_replace/insert/create/undo_edit
- `hack_detector.enabled` must be respected in pipeline
- Missing gold → `missing_gold` flag + warning, don't silently pretend C1–C4 are meaningful
- `traj_analysis` is optional; failures should not break main pipeline
- Run `python -m pytest tests/ -q` after parser/labeler changes
