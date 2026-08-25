#!/bin/bash
set -euo pipefail

if [[ "${HARBOR_PRUNE_DOCKER:-0}" == "1" ]]; then docker container prune -f && docker network prune -f; fi

DATASET=swebenchpro-100
AGENT=custom-openhands-sdk
AGENT_IMPORT_PATH=harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK
VERSION=1.14.0
PROVIDER=hosted_vllm
MODEL_NAME=Qwen3-Coder-30B-A3B-Instruct
SEND_REASONING_CONTENT_MODELS="qwen3"
MAX_RETRIES=2
TIMEOUT_MULTIPLIER=1
N_CONCURRENT="${N_CONCURRENT:-6}"
# Optional smoke: N_TASKS=1 (limits how many dataset tasks to run)
N_TASKS="${N_TASKS:-}"
# Optional task filter: whitespace-separated task names or glob patterns.
INCLUDE_TASK_NAMES="${INCLUDE_TASK_NAMES:-}"
MAX_ITERATIONS=200
TEMPERATURE=0.7

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"
REGISTRY_PATH="${REPO_ROOT}/registry.json"

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_LITELLM_HOST="host.docker.internal"
else
   HOSTED_LITELLM_HOST="$(hostname -I | awk '{print $1}')"
fi

LLM_BASE_URL="http://${HOSTED_LITELLM_HOST}:4001/v1"
LLM_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
# Keep the in-container path short; some Docker daemons hit overlay2 filename
# limits for long mount targets.
CONTAINER_RUNTIME_ROOT="/oh-sdk"
# Default self-contained runtime image. Override with RUNTIME_SOURCE_IMAGE if needed.
DEFAULT_RUNTIME_SOURCE_IMAGE="docker.io/jierun/c-oh-sdk-${VERSION}:v0.5"
RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE:-$DEFAULT_RUNTIME_SOURCE_IMAGE}"
RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH:-/opt/custom-agent-runtime/oh-sdk}"

HOST_RUNTIME_DIR="${RUNTIME_HOST_DIR:-/tmp/harbor-runtime-oh-sdk-${VERSION}}"
if [[ ! -f "$HOST_RUNTIME_DIR/runtime-env.sh" || ! -f "$HOST_RUNTIME_DIR/runtime-select-python.sh" ]]; then
  echo "Extracting runtime from ${RUNTIME_SOURCE_IMAGE} ..."
  if [[ -z "$HOST_RUNTIME_DIR" || "$HOST_RUNTIME_DIR" == "/" || "$HOST_RUNTIME_DIR" == "." ]]; then
    echo "Refusing to reset unsafe runtime directory: $HOST_RUNTIME_DIR" >&2
    exit 1
  fi
  case "$HOST_RUNTIME_DIR" in
    /tmp/harbor-runtime-*|"$REPO_ROOT"/.cache/harbor-runtime-*) ;;
    *)
      if [[ "${ALLOW_RUNTIME_DIR_RESET:-0}" != "1" ]]; then
        echo "Refusing to reset non-standard runtime directory: $HOST_RUNTIME_DIR" >&2
        echo "Set ALLOW_RUNTIME_DIR_RESET=1 only after verifying that path." >&2
        exit 1
      fi
      ;;
  esac
  rm -rf -- "$HOST_RUNTIME_DIR"
  mkdir -p "$HOST_RUNTIME_DIR"
  _cid=$(docker create "$RUNTIME_SOURCE_IMAGE" true)
  docker cp "$_cid:${RUNTIME_IMAGE_SUBPATH%/}/." "$HOST_RUNTIME_DIR/"
  docker rm "$_cid" > /dev/null
  test -f "$HOST_RUNTIME_DIR/runtime-env.sh"
  test -f "$HOST_RUNTIME_DIR/runtime-select-python.sh"
  echo "Runtime extracted to ${HOST_RUNTIME_DIR}"
fi

MOUNTS_JSON="$(
  HOST_RUNTIME_DIR="${HOST_RUNTIME_DIR}" \
  CONTAINER_RUNTIME_ROOT="${CONTAINER_RUNTIME_ROOT}" \
  python -c 'import json, os
mounts = [{
  "type": "bind",
  "source": os.environ["HOST_RUNTIME_DIR"],
  "target": os.environ["CONTAINER_RUNTIME_ROOT"],
  "read_only": True,
}]
print(json.dumps(mounts))'
)"

# DATASET-AGENT-MODEL_NAME-timestamp
JOB_NAME="${DATASET}-${AGENT}-${VERSION}-${MODEL_NAME}-$(date +%Y%m%d%H%M%S)"

extra_args=()
if [[ -n "${N_TASKS}" ]]; then
  extra_args+=(--n-tasks "${N_TASKS}")
fi
if [[ -n "${INCLUDE_TASK_NAMES}" ]]; then
  for task_name in ${INCLUDE_TASK_NAMES}; do
    extra_args+=(--include-task-name "${task_name}")
  done
fi

uv run harbor run --dataset "$DATASET" \
  --registry-path "$REGISTRY_PATH" \
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
