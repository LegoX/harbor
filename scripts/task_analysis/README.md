# Task Analysis Scripts

Utilities for annotating Harbor task datasets. These scripts are intended for
offline dataset maintenance, not for normal `harbor run` execution.

## `tag_task_metadata.py`

Adds or completes task metadata tags in `task.toml`.

The target tag schema is:

```python
[language, area, topic, bug_class]
```

- `language`: primary programming language, for example `python`, `go`,
  `typescript`, `rust`.
- `area`: one of `backend`, `frontend`, `fullstack`, `cli`, `library`,
  `framework`.
- `topic`: framework, library, or focused technical topic.
- `bug_class`: domain-independent defect mechanism, for example
  `missing-fallback`, `incomplete-validation`, `wrong-default`,
  `missing-metadata-propagation`.

Behavior:

- Existing four-part tags are skipped.
- Existing three-part tags with a valid `area` keep their first three tags; the
  script calls the LLM only for `bug_class` and appends it.
- Missing, empty, or legacy tags are regenerated as full four-part tags.
- Difficulty is only written when full tags are regenerated. The three-tag
  append path does not modify existing `difficulty` or `[scoring]` metadata.
- The script is resumable: rerunning skips tasks that already have four-part
  tags.

## Dry Run

Dry runs do not call the LLM and do not write files:

```bash
uv run python scripts/task_analysis/tag_task_metadata.py \
  --datasets-root datasets \
  --dataset swegen-selfmade-260301-260414 \
  --dry-run \
  --max 10
```

## Run With Local GLM-5-FP8

Example using the local LiteLLM proxy on port `4001`:

```bash
env UV_CACHE_DIR=/tmp/harbor-uv-cache PYTHONUNBUFFERED=1 \
  uv run python scripts/task_analysis/tag_task_metadata.py \
    --datasets-root datasets \
    --dataset example-dataset \
    --model GLM-5-FP8 \
    --api-key dummy-key-cf \
    --base-url http://127.0.0.1:4001/v1 \
    --jobs 8 \
    --retries 3
```

For long runs, use tmux and tee logs:

```bash
tmux new-session -d -s task-metadata-run \
  -c /path/to/harbor \
  "env UV_CACHE_DIR=/tmp/harbor-uv-cache PYTHONUNBUFFERED=1 \
    uv run python scripts/task_analysis/tag_task_metadata.py \
      --datasets-root datasets \
      --dataset example-dataset \
      --model GLM-5-FP8 \
      --api-key dummy-key-cf \
      --base-url http://127.0.0.1:4001/v1 \
      --jobs 8 \
      --retries 3 \
    2>&1 | tee logs/manual_runs/task-metadata-run.log"
```

For resume runs that mainly retry prior LLM empty-output failures, lower
concurrency is usually more stable:

```bash
env UV_CACHE_DIR=/tmp/harbor-uv-cache PYTHONUNBUFFERED=1 \
  uv run python scripts/task_analysis/tag_task_metadata.py \
    --datasets-root datasets \
    --dataset example-dataset \
    --model GLM-5-FP8 \
    --api-key dummy-key-cf \
    --base-url http://127.0.0.1:4001/v1 \
    --jobs 2 \
    --retries 5 \
    --retry-delay-sec 2
```

## Monitoring

```bash
tmux capture-pane -pt task-metadata-run -S -80
tail -100 logs/manual_runs/task-metadata-run.log

printf 'updated='
rg -c '^\[updated\]' logs/manual_runs/task-metadata-run.log || true
printf 'skipped='
rg -c '^\[skipped\]' logs/manual_runs/task-metadata-run.log || true
printf 'error='
rg -c '^\[error\]' logs/manual_runs/task-metadata-run.log || true
```

Errors are usually safe to retry after the run finishes. Successful tasks will
be skipped on the next run.

## Validation

```bash
env UV_CACHE_DIR=/tmp/harbor-uv-cache uv run pytest tests/unit/test_task_analysis_tag_task_metadata.py
uv run ruff check scripts/task_analysis/tag_task_metadata.py tests/unit/test_task_analysis_tag_task_metadata.py
uv run ty check scripts/task_analysis/tag_task_metadata.py tests/unit/test_task_analysis_tag_task_metadata.py
```
