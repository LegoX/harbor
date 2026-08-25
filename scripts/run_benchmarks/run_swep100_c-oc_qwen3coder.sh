#!/bin/bash
set -euo pipefail

if [[ "${HARBOR_PRUNE_DOCKER:-0}" == "1" ]]; then docker container prune -f && docker network prune -f; fi

DATASET=swebenchpro-100
AGENT=custom-opencode
AGENT_IMPORT_PATH=harbor.agents.custom.opencode:CustomOpenCode
VERSION=1.14.22
PROVIDER=hosted_vllm
MODEL_NAME=Qwen3-Coder-30B-A3B-Instruct
MAX_RETRIES=2
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
N_CONCURRENT="${N_CONCURRENT:-6}"
# Optional smoke: N_TASKS=1 (limits how many dataset tasks to run)
N_TASKS="${N_TASKS:-}"
TEMPERATURE="${TEMPERATURE:-0.7}"
OPENCODE_DISABLE_STREAMING="${OPENCODE_DISABLE_STREAMING:-true}"

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"
REGISTRY_PATH="${SCRIPT_DIR}/../../registry.json"

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_VLLM_HOST="host.docker.internal"
else
   HOSTED_VLLM_HOST="$(hostname -I | awk '{print $1}')"
fi

# Route through the host-side LiteLLM proxy so OpenCode uses an explicit
# openai-compatible provider while Harbor still records per-trial trajectories.
HOSTED_VLLM_BASE_URL="http://${HOSTED_VLLM_HOST}:4001/v1"
HOSTED_VLLM_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
OPENCODE_MODEL="${PROVIDER}/${MODEL_NAME}"
OPENCODE_CONFIG_CONTENT="$(
  MODEL_NAME="${MODEL_NAME}" \
  OPENCODE_MODEL="${OPENCODE_MODEL}" \
  OPENCODE_DISABLE_STREAMING="${OPENCODE_DISABLE_STREAMING}" \
  python -c 'import json, os
model_name = os.environ["MODEL_NAME"]
opencode_model = os.environ["OPENCODE_MODEL"]
disable_streaming = os.environ["OPENCODE_DISABLE_STREAMING"].lower() in {"1", "true", "yes", "on"}
options = {
    "baseURL": "{env:HOSTED_VLLM_BASE_URL}",
    "apiKey": "{env:HOSTED_VLLM_API_KEY}",
    "headers": {
        "x-harbor-temperature": "{env:OPENCODE_TEMPERATURE}"
    },
}
if disable_streaming:
    options["disableStreaming"] = True
print(json.dumps({
    "$schema": "https://opencode.ai/config.json",
    "model": opencode_model,
    "small_model": opencode_model,
    "enabled_providers": ["hosted_vllm"],
    "provider": {
        "hosted_vllm": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Hosted vLLM",
            "options": options,
            "models": {
                model_name: {
                    "name": model_name,
                },
            },
        },
    },
    "agent": {
        "title": {"model": opencode_model},
        "summary": {"model": opencode_model},
        "compaction": {"model": opencode_model},
    },
}, separators=(",", ":")))'
)"
# Keep the in-container path short; some Docker daemons hit overlay2 filename
# limits for long image volume targets.
CONTAINER_RUNTIME_ROOT="/opencode"
# Default self-contained runtime image. Override with RUNTIME_SOURCE_IMAGE if needed.
DEFAULT_RUNTIME_SOURCE_IMAGE="docker.io/yjiangcm/c-oc-${VERSION}:v0.2"
RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE:-$DEFAULT_RUNTIME_SOURCE_IMAGE}"
RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH:-/opt/custom-agent-runtime/opencode}"

HOST_RUNTIME_DIR="${RUNTIME_HOST_DIR:-/tmp/harbor-runtime-opencode-${VERSION}}"
if [[ ! -f "$HOST_RUNTIME_DIR/runtime-env.sh" || ! -f "$HOST_RUNTIME_DIR/runtime-select-node.sh" || ! -x "$HOST_RUNTIME_DIR/bin/opencode" || ! -x "$HOST_RUNTIME_DIR/bin/node" ]]; then
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
  test -f "$HOST_RUNTIME_DIR/runtime-select-node.sh"
  test -x "$HOST_RUNTIME_DIR/bin/opencode"
  test -x "$HOST_RUNTIME_DIR/bin/node"
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
  --ae "HOSTED_VLLM_BASE_URL=$HOSTED_VLLM_BASE_URL" \
  --ae "HOSTED_VLLM_API_KEY=$HOSTED_VLLM_API_KEY" \
  --ae "OPENCODE_TEMPERATURE=$TEMPERATURE" \
  --ae "OPENCODE_CONFIG_CONTENT=$OPENCODE_CONFIG_CONTENT" \
  --ae "CUSTOM_AGENT_RUNTIME_ROOT=$CONTAINER_RUNTIME_ROOT" \
  --ae "CUSTOM_AGENT_OPENCODE=$CONTAINER_RUNTIME_ROOT/bin/opencode" \
  --ae "CUSTOM_AGENT_RUNTIME_ENV_SCRIPT=$CONTAINER_RUNTIME_ROOT/runtime-env.sh"
