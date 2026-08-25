# Evaluation Analysis Tooling

This fork adds several complementary analysis tools under `scripts/`.

## Job Analysis Pipeline

`scripts/job_analysis/` analyzes Harbor trial directories or aggregated
OpenHands JSONL output. Its stages include:

1. trajectory parsing and patch reconstruction;
2. gold patch and test metadata loading;
3. deterministic localization, pathology, and reward-hack features;
4. optional heuristic or LLM-assisted judging;
5. multi-axis failure labeling;
6. failed/resolved aggregate and comparison reports;
7. optional task, trajectory, and per-instance analyses.

Start with a tracked configuration:

```bash
cd scripts/job_analysis
python run.py --config configs/harbor_sweb_openhands_sdk.yaml
```

Use `configs/openhands_jsonl_full.yaml` for generic aggregated OpenHands JSONL
input. All paths, model names, and optional judge credentials must be supplied
by the caller.

The pipeline writes JSONL instance records, aggregate JSON/Markdown reports,
failed-versus-resolved comparisons, trajectory-quality summaries, and optional
metadata cross-tabs. Generated outputs are not committed by default.

## Task Metadata Tagging

`scripts/task_analysis/tag_task_metadata.py` classifies prepared task
directories and updates their metadata. It supports concurrent requests,
retries, resume behavior, and an OpenAI-compatible endpoint.

```bash
uv run python scripts/task_analysis/tag_task_metadata.py \
  --datasets-root datasets \
  --dataset example-dataset \
  --model example-model \
  --api-key "$LLM_API_KEY" \
  --base-url "$LLM_BASE_URL" \
  --jobs 4
```

Use a small task subset or copied dataset when validating taxonomy changes;
the tool updates task metadata files.

## Runtime and Trajectory Profiling

`scripts/misc/profile_completed_task_trajectories.py` combines Harbor phase
timestamps with LiteLLM call intervals to estimate environment setup, agent
setup, model API time, non-API agent time, verification time, token usage, and
failure rates.

```bash
uv run python scripts/misc/profile_completed_task_trajectories.py \
  /path/to/harbor/jobs/example-job
```

Use these measurements to separate proxy/backend saturation from local agent
or verifier bottlenecks.

## Dataset Checks and Hardening

- `scripts/check_dataset_tasks.py` validates prepared task structure and common
  task configuration expectations.
- `scripts/misc/generate_swebench_verified_nohack.py` creates an egress-hardened
  local SWE-Bench Verified dataset.
- `predict_patch.diff` artifacts provide a stable source for patch-oriented
  analysis even when an agent commits its work.

## Analysis Safety

Trajectories can contain prompts, source code, model responses, URLs, and
credentials accidentally supplied to an agent. Treat raw jobs as sensitive,
redact exported reports, and never publish request bodies without reviewing
their provenance and licensing.
