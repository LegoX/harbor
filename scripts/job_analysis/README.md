# SWE-bench Evaluation Analysis Pipeline

This pipeline performs multi-axis attribution analysis for failed and resolved
SWE-bench, SWE-bench Multilingual, and SWE-bench Pro evaluations. It evolved
from [YJiangcm/swebench-error-analysis](https://github.com/YJiangcm/swebench-error-analysis)
and adds Harbor job input, LiteLLM trajectories, patch reconstruction, and
optional task/trajectory/instance analysis.

## Quick Start

```bash
cd scripts/job_analysis
pip install -r requirements-job-analysis.txt

# Edit log_dir and dataset_dir first.
python run.py --config configs/harbor_sweb_openhands_sdk.yaml
```

## Configuration Profiles

| Profile | Input | Intended use |
| --- | --- | --- |
| [`configs/default.yaml`](configs/default.yaml) | Aggregated OpenHands JSONL | Local development and generic OpenHands output |
| [`configs/openhands_jsonl_full.yaml`](configs/openhands_jsonl_full.yaml) | Aggregated OpenHands JSONL | Enable all optional stages |
| [`configs/harbor_sweb_openhands_sdk.yaml`](configs/harbor_sweb_openhands_sdk.yaml) | Harbor job directory | SWE-bench Verified |
| [`configs/harbor_swem_openhands_sdk.yaml`](configs/harbor_swem_openhands_sdk.yaml) | Harbor job directory | SWE-bench Multilingual |
| [`configs/harbor_swep_openhands_sdk.yaml`](configs/harbor_swep_openhands_sdk.yaml) | Harbor job directory | SWE-bench Pro |

See [configuration reference](configs/README.md) for every field.

Common overrides:

```bash
python run.py --config configs/default.yaml --include-resolved

export ANTHROPIC_API_KEY=...
python run.py --config configs/default.yaml --judge

python run.py --config configs/harbor_sweb_openhands_sdk.yaml \
  --no-task-analysis --no-traj-analysis --no-instance-analysis
```

## Pipeline Stages

```text
Stage 1  Parse trajectories and reconstruct patches
Stage 2  Load gold data and parse diffs
Stage 3  Extract deterministic localization, pathology, and hack features
Stage 4  Apply the Tier-1 heuristic or optional LLM judge
Stage 5  Assign multi-axis primary and secondary attribution labels
Stage 6  Generate failed, resolved, and comparison reports
Stage 7  Optionally run task, trajectory, and instance analyses
```

Feature definitions, label semantics, gold-aware boundaries, and the output
schema are documented in [`design.md`](design.md).

## Input Layouts

| | Aggregated OpenHands JSONL | Harbor job |
| --- | --- | --- |
| `trajectory_layout` | `openhands_jsonl` | `harbor_job` |
| Trajectory source | `log_dir/trajectory_file` | `<trial>/agent/litellm-trajectory.jsonl` |
| Gold source | `log_dir/test_file` | `dataset_dir/<id>/tests/config.json` |
| Resolution source | `report_file` | `<trial>/result.json` and `verifier/report.json` |

## Outputs

The default output directory is `output/<input-directory-name>`.

| Output | Contents |
| --- | --- |
| `instances.jsonl` | Per-instance features and labels |
| `instances_failed.jsonl` / `instances_resolved.jsonl` | Split instance records |
| `report_failed.*` / `report_resolved.*` | Aggregate distributions |
| `score_comparison.*` | Failed-versus-resolved feature comparison |
| `report_task_analysis.*` | Difficulty, domain, and bug-type analysis |
| `traj_analysis/` | Trajectory quality scoring and comparisons |
| `instance_analysis/` | Metadata cross-tabs and correlations |

Generated outputs may contain source code, prompts, and model responses. Review
and redact them before publication.

## Development

```bash
python -m pytest tests -q
```

When changing trajectory or patch parsing, inspect `trajectory_metadata` in
the generated instance records and test both aggregated OpenHands JSONL and
Harbor job layouts.
