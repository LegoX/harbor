#!/bin/bash
set -euo pipefail

docker container prune -f && docker network prune -f

DATASET=swerebenchv2-no-python
DATASET_PATH="datasets/${DATASET}"
AGENT=oracle
N_CONCURRENT="${N_CONCURRENT:-8}"
MAX_RETRIES="${MAX_RETRIES:-2}"
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-5}"
# The 100T containerd filesystem normally has enough room for this run. Set a
# positive interval to opt into periodic BuildKit cache pruning if needed.
BUILDER_PRUNE_INTERVAL_SEC="${BUILDER_PRUNE_INTERVAL_SEC:-0}"
BUILDER_PRUNE_KEEP_STORAGE="${BUILDER_PRUNE_KEEP_STORAGE:-200GB}"
# Optional smoke: N_TASKS=1 limits how many dataset tasks to run.
N_TASKS="${N_TASKS:-}"

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

JOB_NAME="${DATASET}-${AGENT}-$(date +%Y%m%d%H%M%S)"

prune_builder_cache_periodically() {
  while true; do
    docker builder prune --all --force \
      --keep-storage "$BUILDER_PRUNE_KEEP_STORAGE" >/dev/null || true
    sleep "$BUILDER_PRUNE_INTERVAL_SEC"
  done
}

if ((BUILDER_PRUNE_INTERVAL_SEC > 0)); then
  prune_builder_cache_periodically &
  BUILDER_PRUNER_PID=$!
  trap 'kill "$BUILDER_PRUNER_PID" 2>/dev/null || true' EXIT
fi

extra_args=()
if [[ -n "$N_TASKS" ]]; then
  extra_args+=(--n-tasks "$N_TASKS")
fi

uv run harbor run --path "$DATASET_PATH" \
  --agent "$AGENT" \
  --job-name "$JOB_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  "${extra_args[@]}" \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --retry-exclude AgentTimeoutError \
  --max-retries "$MAX_RETRIES"
