#!/bin/bash
# Full SWE-Bench Verified nohack run for OpenHands SDK on coder_glm52.
# This uses a local copy of swebench-verified with agent egress limited to the
# configured LiteLLM endpoint. Verifier egress remains public so the SWE-Bench
# parser can resolve runtime dependencies. If the selected local nohack registry
# is missing, this script generates it from the root registry.json first. Limited
# smoke runs use a separate registry file so they do not replace the full run
# registry.
#
# Start the proxy with:
#
#   CONFIG_NAME=litellm_config_glm52 LITELLM_PORT=4003 \
#     LITELLM_STICKY_ROUTING_ALIASES= bash scripts/serve_llm/serve_litellm.sh
#
# Smoke test:
#   NOHACK_GENERATE_LIMIT=1 N_TASKS=1 N_CONCURRENT=1 \
#     bash scripts/run_benchmarks/run_sweb_nohack_c-oh-sdk_coder_glm52.sh

set -euo pipefail

DATASET=swebench-verified-nohack
AGENT=custom-openhands-sdk
AGENT_IMPORT_PATH=harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK
VERSION=1.14.0
PROVIDER=hosted_vllm
MODEL_NAME=coder_glm52
SEND_REASONING_CONTENT_MODELS="coder_glm52,glm-5.2,glm-5"
MAX_RETRIES=2
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
N_CONCURRENT="${N_CONCURRENT:-2}"
N_TASKS="${N_TASKS:-}"
MAX_ITERATIONS=200
TEMPERATURE=1.0

NOHACK_AUTO_GENERATE="${NOHACK_AUTO_GENERATE:-1}"
NOHACK_REFRESH="${NOHACK_REFRESH:-0}"
NOHACK_GENERATE_LIMIT="${NOHACK_GENERATE_LIMIT:-}"

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

ROOT_REGISTRY_PATH="${REPO_ROOT}/registry.json"
REGISTRY_BASE_PATH="${REPO_ROOT}/scripts/git_ignore/hack_control/registry.swebench_verified_nohack.json"
NOHACK_GENERATOR="${REPO_ROOT}/scripts/misc/generate_swebench_verified_nohack.py"
NOHACK_SOURCE_TASK_DIR="${REPO_ROOT}/datasets/swebench-verified-local"
NOHACK_TASK_DIR="${REPO_ROOT}/datasets/swebench-verified-nohack"

if [[ -n "$NOHACK_GENERATE_LIMIT" ]]; then
  if [[ ! "$NOHACK_GENERATE_LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "NOHACK_GENERATE_LIMIT must be a positive integer: $NOHACK_GENERATE_LIMIT" >&2
    exit 1
  fi
  REGISTRY_PATH="${REGISTRY_BASE_PATH%.json}.limit${NOHACK_GENERATE_LIMIT}.json"
else
  REGISTRY_PATH="$REGISTRY_BASE_PATH"
fi

if [[ ! -f "$REGISTRY_PATH" || "$NOHACK_REFRESH" == "1" ]]; then
  if [[ "$NOHACK_AUTO_GENERATE" != "1" ]]; then
    echo "Missing nohack registry: $REGISTRY_PATH" >&2
    echo "Generate it with:" >&2
    echo "  NOHACK_AUTO_GENERATE=1 bash $0" >&2
    exit 1
  fi

  generator_args=(
    --root-registry-path "$ROOT_REGISTRY_PATH"
    --source-task-dir "$NOHACK_SOURCE_TASK_DIR"
    --task-dir "$NOHACK_TASK_DIR"
    --output-registry-path "$REGISTRY_PATH"
  )
  if [[ "$NOHACK_REFRESH" == "1" ]]; then
    generator_args+=(--overwrite --download-overwrite)
  fi
  if [[ -n "$NOHACK_GENERATE_LIMIT" ]]; then
    generator_args+=(--limit "$NOHACK_GENERATE_LIMIT")
  fi
  uv run python "$NOHACK_GENERATOR" "${generator_args[@]}"
fi

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_LITELLM_HOST="host.docker.internal"
else
   HOSTED_LITELLM_HOST="$(hostname -I | awk '{print $1}')"
fi

LITELLM_PORT="${LITELLM_PORT:-4003}"
LLM_BASE_URL="http://${HOSTED_LITELLM_HOST}:${LITELLM_PORT}/v1"
LLM_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
AGENT_ALLOWED_LLM_HOST="${AGENT_ALLOWED_LLM_HOST:-$HOSTED_LITELLM_HOST}"

CONTAINER_RUNTIME_ROOT="/opt/custom-agent-runtime/oh-sdk"
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

JOB_NAME="${DATASET}-${AGENT}-${VERSION}-${MODEL_NAME}-$(date +%Y%m%d%H%M%S)"

extra_args=()
if [[ -n "${N_TASKS}" ]]; then
  extra_args+=(--n-tasks "${N_TASKS}")
fi

uv run harbor run --dataset "$DATASET" \
  --registry-path "$REGISTRY_PATH" \
  --agent-import-path "$AGENT_IMPORT_PATH" \
  --job-name "$JOB_NAME" \
  --mounts-json "$MOUNTS_JSON" \
  --model "$PROVIDER/$MODEL_NAME" \
  --agent-extra-allowed-host "$AGENT_ALLOWED_LLM_HOST" \
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
