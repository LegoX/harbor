#!/bin/bash
# swerebenchv2-200 run for the plain OpenHands SDK agent on GLM-5.2-FP8.
# Start the LiteLLM proxy with:
#
#   CONFIG_NAME=litellm_config_glm52fp8 LITELLM_PORT=4002 \
#     LITELLM_STICKY_ROUTING_ALIASES= bash scripts/serve_llm/serve_litellm.sh
#
# Smoke test:
#   N_TASKS=1 N_CONCURRENT=1 \
#     bash scripts/run_benchmarks/run_swerebenchv2_200_c-oh-sdk_glm52fp8.sh

set -euo pipefail

# docker container prune -f && docker network prune -f

DATASET=swerebenchv2-200-260429
DATASET_PATH="datasets/${DATASET}"
AGENT=custom-openhands-sdk
AGENT_IMPORT_PATH=harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK
VERSION=1.14.0
PROVIDER=hosted_vllm
MODEL_NAME=GLM-5.2-FP8
SEND_REASONING_CONTENT_MODELS="glm-5.2,glm-5"
MAX_RETRIES=2
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
N_CONCURRENT="${N_CONCURRENT:-2}"
# Optional smoke: N_TASKS=1 (limits how many dataset tasks to run)
N_TASKS="${N_TASKS:-}"
MAX_ITERATIONS=200
TEMPERATURE=1.0

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_LITELLM_HOST="host.docker.internal"
else
   HOSTED_LITELLM_HOST="$(hostname -I | awk '{print $1}')"
fi

LITELLM_PORT="${LITELLM_PORT:-4002}"
LLM_BASE_URL="http://${HOSTED_LITELLM_HOST}:${LITELLM_PORT}/v1"
LLM_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
# Stable in-container path that the custom agent uses to locate the mounted runtime.
CONTAINER_RUNTIME_ROOT="/opt/custom-agent-runtime/oh-sdk"
# Default self-contained runtime image. Override with RUNTIME_SOURCE_IMAGE if needed.
DEFAULT_RUNTIME_SOURCE_IMAGE="docker.io/jierun/c-oh-sdk-${VERSION}:v0.5"
RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE:-$DEFAULT_RUNTIME_SOURCE_IMAGE}"
RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH:-$CONTAINER_RUNTIME_ROOT}"

MOUNTS_JSON="$(
  RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE}" \
  RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH}" \
  CONTAINER_RUNTIME_ROOT="${CONTAINER_RUNTIME_ROOT}" \
  python -c 'import json, os
mounts = [{
  "type": "image",
  "source": os.environ["RUNTIME_SOURCE_IMAGE"],
  "target": os.environ["CONTAINER_RUNTIME_ROOT"],
  "read_only": True,
  "image": {
    "subpath": os.environ["RUNTIME_IMAGE_SUBPATH"].lstrip("/"),
  },
}]
print(json.dumps(mounts))'
)"

# DATASET-AGENT-MODEL_NAME-timestamp
JOB_NAME="${DATASET}-${AGENT}-${VERSION}-${MODEL_NAME}-$(date +%Y%m%d%H%M%S)"

extra_args=()
if [[ -n "${N_TASKS}" ]]; then
  extra_args+=(--n-tasks "${N_TASKS}")
fi

uv run harbor run --path "$DATASET_PATH" \
  --agent-import-path "$AGENT_IMPORT_PATH" \
  --job-name "$JOB_NAME" \
  --mounts-json "$MOUNTS_JSON" \
  --model "$PROVIDER/$MODEL_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  "${extra_args[@]}" \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --max-retries "$MAX_RETRIES" \
  --ak "version=$VERSION" \
  --ak "temperature=$TEMPERATURE" \
  --ak "send_reasoning_content_models=$SEND_REASONING_CONTENT_MODELS" \
  --ak "max_iterations=$MAX_ITERATIONS" \
  --ae "LLM_BASE_URL=$LLM_BASE_URL" \
  --ae "LLM_API_KEY=$LLM_API_KEY" \
  --ae "CUSTOM_AGENT_RUNTIME_ROOT=$CONTAINER_RUNTIME_ROOT" \
  --ae "CUSTOM_AGENT_PYTHON=$CONTAINER_RUNTIME_ROOT/bin/python"
