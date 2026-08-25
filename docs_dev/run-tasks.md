# Running Tasks

This repository includes shell wrappers for repeatable local evaluations:

- `scripts/run_benchmarks/` contains benchmark launchers and `resume_job.sh`.
- `scripts/run_datasets/` contains launchers for prepared local datasets.

Most wrappers call `uv run harbor run` with a fixed dataset, agent, runtime,
and model combination. Concurrency and smoke-test parameters remain overridable
through environment variables.

Together with Harbor's saved job configuration and versioned runtime images,
these tracked wrappers make evaluation inputs explicit and allow a job workflow
to be repeated or resumed without reconstructing an undocumented command. For
comparable reruns, record the immutable inputs and execution context listed in
the [reproducibility contract](./reproducibility.md).

Repeatable job inputs do not guarantee bit-for-bit identical results. Sampling,
remote model updates, hardware, task-image changes, and infrastructure timing
can still affect an evaluation.

## Common Overrides

| Variable | Purpose |
| --- | --- |
| `N_CONCURRENT` | Maximum concurrent trials |
| `N_TASKS` | Limit the dataset for smoke tests |
| `MAX_RETRIES` | Maximum retry count per trial |
| `TIMEOUT_MULTIPLIER` | Scale Harbor phase timeouts |
| `MAX_TURNS` / `MAX_ITERATIONS` | Limit agent interaction steps |
| `TEMPERATURE` | Override the agent/model temperature |
| `LITELLM_PORT` | Local LiteLLM proxy port |
| `RUNTIME_SOURCE_IMAGE` | Custom mounted runtime image |
| `RUNTIME_IMAGE_SUBPATH` | Runtime location inside the source image |
| `LITELLM_MASTER_KEY` | Master key shared with the local LiteLLM proxy |

Example:

```bash
N_TASKS=5 N_CONCURRENT=4 LITELLM_PORT=4001 \
  bash scripts/run_benchmarks/run_swerebenchv2_200_c-cc_glm5.1fp8.sh
```

Use the dedicated `*_nohack_*` wrappers for agent-egress restrictions. Regular
wrappers intentionally retain public networking for backward compatibility.

## Resuming a Job

`scripts/run_benchmarks/resume_job.sh` wraps `harbor jobs resume` for local
benchmark workflows. It can:

- restore any masked agent environment value from the same-named variable in
  the current shell, without writing plaintext values back to `config.json`;
- update concurrency without manually editing every trial configuration;
- discard incomplete or invalid trial directories;
- match completed trials by task and agent identity when non-essential saved
  configuration has drifted.

Preview the command first:

```bash
LLM_API_KEY="${LLM_API_KEY}" \
N_CONCURRENT=8 \
DRY_RUN=1 \
  bash scripts/run_benchmarks/resume_job.sh /path/to/job
```

Then remove `DRY_RUN=1` to resume. Unmasked explicit values in the saved
configuration take precedence over host defaults. Resume fails when a masked
value cannot be restored; `ALLOW_MASKED_SECRETS=1` is an explicit escape hatch
for jobs that intentionally use a masked-looking literal.

## Automatic Patch Artifacts

Every trial has a `PredictPatchConfig`, enabled by default. After the agent
finishes and before verification changes the workspace, Harbor writes the
agent's complete Git diff to:

```text
jobs/<job>/<trial>/artifacts/predict_patch.diff
```

The default repository candidates are `/testbed`, `/app/src`, `/workspace`,
`/app`, and `/repo`. If none matches, Harbor performs a bounded search for an
existing `.git` directory while excluding system and Harbor mount paths.

Before agent execution, Harbor selects the first explicit Git repository and
retains its commit SHA in the Harbor process. For datasets that remove `.git`,
Harbor can first initialize a one-commit baseline at an explicitly configured
candidate path when `init_baseline=True`; existing Git repositories are still
probed when that option is false. Patch capture stages tracked and untracked changes and diffs
against the retained SHA, so agent-side commits remain visible. If baseline
discovery fails, capture falls back to the configured tag or `HEAD` for
compatibility. It then resets the index without changing the working tree.
Baseline creation and capture are best-effort and do not fail the trial.

The public configuration is:

```python
PredictPatchConfig(
    enabled=True,
    repo_paths=["/testbed", "/app/src", "/workspace", "/app", "/repo"],
    output_filename="predict_patch.diff",
    auto_discover=True,
    init_baseline=True,
    baseline_tag="harbor-baseline",
)
```

## Wrapper Safety

Tracked wrappers no longer prune Docker state by default. Set
`HARBOR_PRUNE_DOCKER=1` to opt into pruning stopped containers and unused
networks. The no-hack Pro wrapper likewise requires
`HARBOR_STOP_EXISTING_CONTAINERS=1` before stopping containers that look like
old Harbor trials. Runtime extraction refuses to recursively reset a
non-standard directory unless `ALLOW_RUNTIME_DIR_RESET=1` is set.

New wrappers should expose mutable operational settings as environment
variables, document destructive behavior at the top, and support a small
`N_TASKS=1` smoke run.
