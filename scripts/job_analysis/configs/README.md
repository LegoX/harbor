# Configuration Reference

All job-analysis profiles are YAML files loaded by `src/config.py`. Relative
paths are resolved from `scripts/job_analysis/`, not from the profile's own
directory.

```bash
python run.py --config configs/default.yaml
```

## Choose a Profile

| Profile | Layout | Use case |
| --- | --- | --- |
| `default.yaml` | `openhands_jsonl` | Minimal aggregated OpenHands input |
| `openhands_jsonl_full.yaml` | `openhands_jsonl` | Aggregated OpenHands input with optional stages |
| `harbor_sweb_openhands_sdk.yaml` | `harbor_job` | SWE-bench Verified Harbor job |
| `harbor_swem_openhands_sdk.yaml` | `harbor_job` | SWE-bench Multilingual Harbor job |
| `harbor_swep_openhands_sdk.yaml` | `harbor_job` | SWE-bench Pro Harbor job |

## Minimum Input

Aggregated OpenHands JSONL:

```yaml
data:
  log_dir: "<path-to-openhands-job>"
```

Harbor job:

```yaml
data:
  log_dir: "<path-to-harbor-job>"
  dataset_dir: "<path-to-dataset>"
```

## Sections

### `data`

| Field | Default | Meaning |
| --- | --- | --- |
| `log_dir` | `""` | Required evaluation output root |
| `trajectory_layout` | `openhands_jsonl` | `openhands_jsonl` or `harbor_job` |
| `trajectory_file` | `output.critic_attempt_1.jsonl` | Aggregated trajectory relative to `log_dir` |
| `trajectory_subpath` | `agent/litellm-trajectory.jsonl` | Per-trial Harbor trajectory path |
| `gold_source` | `jsonl` | `jsonl` or `harbor_dataset` |
| `dataset_dir` | `""` | Dataset root; required for instance analysis |
| `report_file` | `output.critic_attempt_1.report.json` | Aggregated result report |
| `trial_result_file` | `result.json` | Harbor trial result filename |
| `trial_report_subpath` | `verifier/report.json` | Harbor verifier report path |
| `max_iterations_default` | `0` | Fallback when a trial config is unavailable |

### `output`

| Field | Default | Meaning |
| --- | --- | --- |
| `dir` | Derived | Output directory; defaults to `output/<input-name>` |
| `instances_jsonl` | `instances.jsonl` | Combined instance records |
| `report_json` | `report.json` | Base aggregate report name |

### `analysis`

| Field | Default | Meaning |
| --- | --- | --- |
| `include_resolved` | `false` | Analyze resolved instances and create comparisons |
| `include_errors` | `true` | Include infrastructure/runtime errors |
| `include_empty_patch` | `true` | Include instances without a reconstructed patch |

### `features`

| Field | Default | Meaning |
| --- | --- | --- |
| `loop_threshold` | `3` | Repeated operations required for a loop signal |
| `premature_stop_threshold` | `0.3` | Remaining-iteration ratio for premature-stop detection |
| `tool_error_storm_threshold` | `5` | Consecutive tool errors required for an error storm |

### `judge`

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Use an LLM judge instead of heuristic-only fallback |
| `model` | `claude-sonnet-4-6` | Judge model name |
| `api_key` | Environment | Usually supplied as `ANTHROPIC_API_KEY` |
| `max_trajectory_chars` | `8000` | Maximum summarized trajectory input |

### Optional Stages

| Setting | Default | Requirement | Output |
| --- | --- | --- | --- |
| `task_analysis.enabled` | `false` | Gold task data | `report_task_analysis.*` |
| `instance_analysis.enabled` | `true` | `dataset_dir` and per-task `metadata.json` | `instance_analysis/` |
| `traj_analysis.enabled` | Profile-specific | History or readable LiteLLM JSONL | `traj_analysis/` |

Disable optional stages for a faster parser/labeler iteration:

```bash
python run.py --config configs/harbor_sweb_openhands_sdk.yaml \
  --no-task-analysis --no-traj-analysis --no-instance-analysis
```

## Top-Level Metadata

- `scaffold`: agent/scaffold identifier written to output records.
- `model`: model identifier written to output records; supply it explicitly.
- `skip_main_pipeline`: run only selected optional stages.
- `taxonomy_version` and `judge_version`: output schema provenance.

## CLI Overrides

- `--include-resolved`
- `--judge`
- `--task-analysis` / `--no-task-analysis`
- `--traj-analysis` / `--no-traj-analysis`
- `--instance-analysis` / `--no-instance-analysis`
- `--skip-main-pipeline`

For a new profile, copy the closest existing layout, change only input paths
and provenance metadata first, validate `instances.jsonl`, and then enable
optional stages.
