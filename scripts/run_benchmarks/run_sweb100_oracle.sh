#!/bin/bash
set -euo pipefail

DATASET=swebench-verified-100
AGENT=oracle
N_CONCURRENT="${N_CONCURRENT:-1}"
N_TASKS="${N_TASKS:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REGISTRY_PATH="${REPO_ROOT}/registry.json"
cd "$REPO_ROOT"

JOB_NAME="${DATASET}-${AGENT}-$(date +%Y%m%d%H%M%S)"

extra_args=()
if [[ -n "$N_TASKS" ]]; then
  extra_args+=(--n-tasks "$N_TASKS")
fi

uv run harbor run --dataset "$DATASET" \
  --registry-path "$REGISTRY_PATH" \
  --agent "$AGENT" \
  --job-name "$JOB_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  "${extra_args[@]}"
