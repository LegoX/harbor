#!/bin/bash
# swegen-selfmade-260301-260622-top5k run for OpenCode 1.18.7 on GLM-5.2-FP8.
#
# Start the proxy with:
#
#   CONFIG_NAME=litellm_config_glm52fp8 LITELLM_PORT=4002 \
#     LITELLM_STICKY_ROUTING_ALIASES= bash scripts/serve_llm/serve_litellm.sh
#
# Smoke test:
#   N_TASKS=1 N_CONCURRENT=1 \
#     bash scripts/run_datasets/run_swegen-selfmade-260301-260622-top5k_c-oc-1.18.7_glm52fp8.sh

set -euo pipefail

# docker container prune -f && docker network prune -f

DATASET=swegen-selfmade-260301-260622-top5k
AGENT=custom-opencode
AGENT_IMPORT_PATH=harbor.agents.custom.opencode:CustomOpenCode
VERSION=1.18.7
PROVIDER=hosted_vllm
MODEL_NAME=GLM-5.2-FP8
LITELLM_PORT="${LITELLM_PORT:-4002}"
MAX_RETRIES="${MAX_RETRIES:-2}"
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-15}"
N_CONCURRENT="${N_CONCURRENT:-48}"
# Keep total trial concurrency high while preventing a cold-start wave from
# overloading Docker's embedded BuildKit with too many independent sessions.
HARBOR_DOCKER_BUILD_CONCURRENCY="${HARBOR_DOCKER_BUILD_CONCURRENCY:-8}"
export HARBOR_DOCKER_BUILD_CONCURRENCY
# Optional smoke: N_TASKS=1 limits how many dataset tasks to run.
N_TASKS="${N_TASKS:-}"
TEMPERATURE="${TEMPERATURE:-1.0}"
OPENCODE_DISABLE_STREAMING="${OPENCODE_DISABLE_STREAMING:-true}"
# Do not inherit a multi-platform builder that may have been selected while
# publishing the runtime image. Harbor starts many Compose builds in parallel,
# and those evaluations should use Docker's local default builder.
BUILDX_BUILDER="${BUILDX_BUILDER:-default}"
export BUILDX_BUILDER

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

DATASET_PATH="${DATASET_PATH:-${REPO_ROOT}/datasets/${DATASET}}"

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_VLLM_HOST="host.docker.internal"
else
   HOSTED_VLLM_HOST="$(hostname -I | awk '{print $1}')"
fi

# Route through the host-side LiteLLM proxy so OpenCode uses an explicit
# openai-compatible provider while Harbor still records per-trial trajectories.
HOSTED_VLLM_BASE_URL="http://${HOSTED_VLLM_HOST}:${LITELLM_PORT}/v1"
HOSTED_VLLM_API_KEY="dummy-key-cf"
OPENCODE_MODEL="${PROVIDER}/${MODEL_NAME}"
OPENCODE_CONFIG_CONTENT="$(
  MODEL_NAME="${MODEL_NAME}" \
  OPENCODE_MODEL="${OPENCODE_MODEL}" \
  OPENCODE_DISABLE_STREAMING="${OPENCODE_DISABLE_STREAMING}" \
  uv run python -c 'import json, os
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

# Stable in-container path that the custom agent uses to locate the mounted runtime.
CONTAINER_RUNTIME_ROOT="/opt/custom-agent-runtime/opencode"
# Default self-contained runtime image. Override with RUNTIME_SOURCE_IMAGE if needed.
DEFAULT_RUNTIME_SOURCE_IMAGE="docker.io/jierun/c-oc-${VERSION}:v0.1"
RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE:-$DEFAULT_RUNTIME_SOURCE_IMAGE}"
RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH:-$CONTAINER_RUNTIME_ROOT}"

case "$(uname -m)" in
  x86_64|amd64)
    DEFAULT_RUNTIME_PLATFORM="linux/amd64"
    ;;
  aarch64|arm64)
    DEFAULT_RUNTIME_PLATFORM="linux/arm64"
    ;;
  *)
    echo "Unsupported host architecture for the OpenCode runtime: $(uname -m)" >&2
    exit 1
    ;;
esac
RUNTIME_PLATFORM="${RUNTIME_PLATFORM:-$DEFAULT_RUNTIME_PLATFORM}"

case "$RUNTIME_PLATFORM" in
  linux/amd64)
    EXPECTED_RUNTIME_ARCH="amd64"
    ;;
  linux/arm64)
    EXPECTED_RUNTIME_ARCH="arm64"
    ;;
  *)
    echo "Unsupported RUNTIME_PLATFORM: $RUNTIME_PLATFORM" >&2
    exit 1
    ;;
esac

# Image mounts use the locally resolved image. Pull and validate the requested
# platform explicitly so a prior cross-platform pull cannot leave this tag
# pointing at an incompatible architecture.
docker pull --platform "$RUNTIME_PLATFORM" "$RUNTIME_SOURCE_IMAGE"
ACTUAL_RUNTIME_ARCH="$(
  docker image inspect "$RUNTIME_SOURCE_IMAGE" --format '{{.Architecture}}'
)"
if [[ "$ACTUAL_RUNTIME_ARCH" != "$EXPECTED_RUNTIME_ARCH" ]]; then
  echo "Runtime image architecture mismatch: expected $EXPECTED_RUNTIME_ARCH, got $ACTUAL_RUNTIME_ARCH" >&2
  exit 1
fi

MOUNTS_JSON="$(
  RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE}" \
  RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH}" \
  CONTAINER_RUNTIME_ROOT="${CONTAINER_RUNTIME_ROOT}" \
  uv run python -c 'import json, os
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
  --ae "HOSTED_VLLM_BASE_URL=$HOSTED_VLLM_BASE_URL" \
  --ae "HOSTED_VLLM_API_KEY=$HOSTED_VLLM_API_KEY" \
  --ae "OPENCODE_TEMPERATURE=$TEMPERATURE" \
  --ae "OPENCODE_CONFIG_CONTENT=$OPENCODE_CONFIG_CONTENT" \
  --ae "CUSTOM_AGENT_RUNTIME_ROOT=$CONTAINER_RUNTIME_ROOT" \
  --ae "CUSTOM_AGENT_OPENCODE=$CONTAINER_RUNTIME_ROOT/bin/opencode" \
  --ae "CUSTOM_AGENT_RUNTIME_ENV_SCRIPT=$CONTAINER_RUNTIME_ROOT/runtime-env.sh"
