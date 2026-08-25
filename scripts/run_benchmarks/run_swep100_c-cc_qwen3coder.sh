#!/bin/bash
set -euo pipefail

if [[ "${HARBOR_PRUNE_DOCKER:-0}" == "1" ]]; then docker container prune -f && docker network prune -f; fi

DATASET=swebenchpro-100
AGENT=custom-claude-code
AGENT_IMPORT_PATH=harbor.agents.custom.claude_code:CustomClaudeCode
VERSION=2.1.118
MODEL_NAME=Qwen3-Coder-30B-A3B-Instruct
MAX_RETRIES=2
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
N_CONCURRENT="${N_CONCURRENT:-6}"
# Optional smoke: N_TASKS=1 (limits how many dataset tasks to run)
N_TASKS="${N_TASKS:-}"
MAX_TURNS=200
TEMPERATURE="${TEMPERATURE:-0.7}"

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"
REGISTRY_PATH="${SCRIPT_DIR}/../../registry.json"

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_LITELLM_HOST="host.docker.internal"
else
   HOSTED_LITELLM_HOST="$(hostname -I | awk '{print $1}')"
fi

ANTHROPIC_BASE_URL="http://${HOSTED_LITELLM_HOST}:4001"
ANTHROPIC_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
# Keep the in-container path short; some Docker daemons hit overlay2 filename
# limits for long image volume targets.
CONTAINER_RUNTIME_ROOT="/cc"
# Default self-contained runtime image. Override with RUNTIME_SOURCE_IMAGE if needed.
DEFAULT_RUNTIME_SOURCE_IMAGE="docker.io/jierun/c-cc-${VERSION}:v0.2"
RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE:-$DEFAULT_RUNTIME_SOURCE_IMAGE}"
RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH:-/opt/custom-agent-runtime/claude-code}"

HOST_RUNTIME_DIR="${RUNTIME_HOST_DIR:-/tmp/harbor-runtime-cc-${VERSION}}"
if [[ ! -f "$HOST_RUNTIME_DIR/runtime-env.sh" || ! -f "$HOST_RUNTIME_DIR/runtime-select-node.sh" || ! -x "$HOST_RUNTIME_DIR/bin/claude" || ! -x "$HOST_RUNTIME_DIR/bin/node" ]]; then
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
  test -x "$HOST_RUNTIME_DIR/bin/claude"
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
  --model "$MODEL_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  "${extra_args[@]}" \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --max-retries "$MAX_RETRIES" \
  --ak "version=$VERSION" \
  --ak "max_turns=$MAX_TURNS" \
  --ak "temperature=$TEMPERATURE" \
  --ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \
  --ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" \
  --ae "ANTHROPIC_MODEL=$MODEL_NAME" \
  --ae "CUSTOM_AGENT_RUNTIME_ROOT=$CONTAINER_RUNTIME_ROOT" \
  --ae "CUSTOM_AGENT_CLAUDE=$CONTAINER_RUNTIME_ROOT/bin/claude" \
  --ae "CUSTOM_AGENT_RUNTIME_ENV_SCRIPT=$CONTAINER_RUNTIME_ROOT/runtime-env.sh"
