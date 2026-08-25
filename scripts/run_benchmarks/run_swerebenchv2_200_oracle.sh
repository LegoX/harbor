#!/bin/bash
set -euo pipefail

if [[ "${HARBOR_PRUNE_DOCKER:-0}" == "1" ]]; then docker container prune -f && docker network prune -f; fi

DATASET=swerebenchv2-200-260429
DATASET_PATH="datasets/${DATASET}"
AGENT=oracle
N_CONCURRENT="${N_CONCURRENT:-12}"
MAX_RETRIES="${MAX_RETRIES:-0}"
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

JOB_NAME="${DATASET}-${AGENT}-$(date +%Y%m%d%H%M%S)"

uv run harbor run --path "$DATASET_PATH" \
  --agent "$AGENT" \
  --job-name "$JOB_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --max-retries "$MAX_RETRIES"
