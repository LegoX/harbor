#!/bin/bash
# Full swebench-verified variant of run_sweb100_c-oh-sdk-condense_glm5fp8.sh that
# enables the OpenHands SDK LLMSummarizingCondenser for context condensation.
#
# This script uses an image-mounted runtime (type="image", read_only=true),
# so the runtime entrypoint
#   scripts/agent_runtimes/openhands-sdk/1.14.0/runtime/run_openhands_harbor.py
# is loaded straight from the Docker image. Condenser wiring requires the
# runtime image to be >= v0.3 (image digest contains the
# CONDENSER_CONFIG_JSON / LLMSummarizingCondenser code paths). To rebuild:
#
#   bash scripts/agent_runtimes/openhands-sdk/1.14.0/build-runtime-image.sh \
#     --image docker.io/jierun/c-oh-sdk-1.14.0:v0.X --push
#
# then bump DEFAULT_RUNTIME_SOURCE_IMAGE below to the new tag.
#
# -----------------------------------------------------------------------------
# Smoke test + verification
# -----------------------------------------------------------------------------
# 1. Run a single task to verify condenser wiring end-to-end:
#
#      N_TASKS=1 N_CONCURRENT=1 \
#        bash scripts/run_benchmarks/run_sweb_c-oh-sdk-condense_glm5fp8.sh
#
# 2. After the trial completes, locate the agent log and verify:
#
#      JOB_DIR=$(ls -td jobs/swebench-verified-custom-openhands-sdk-condense-* | head -1)
#      grep -R "Enabled LLMSummarizingCondenser" "$JOB_DIR"/*/logs/agent/openhands_sdk.txt
#
#    A matching line (model=..., max_size=..., keep_first=..., max_tokens=...)
#    confirms the runtime parsed CONDENSER_CONFIG_JSON and attached the
#    LLMSummarizingCondenser to the SDK Agent.
#
# 3. For trials long enough to hit the trigger, also check that the SDK
#    actually fired condensation:
#
#      grep -RE "triggering condensation|Condensation" \
#        "$JOB_DIR"/*/logs/agent/openhands_sdk.txt
#
#    or inspect the trajectory.json for Condensation events.

set -euo pipefail

# docker container prune -f && docker network prune -f

DATASET=swebench-verified
AGENT=custom-openhands-sdk-condense
AGENT_IMPORT_PATH=harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK
VERSION=1.14.0
PROVIDER=hosted_vllm
MODEL_NAME=GLM-5-FP8
SEND_REASONING_CONTENT_MODELS="glm-5"
MAX_RETRIES=2
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
N_CONCURRENT="${N_CONCURRENT:-6}"
# Optional smoke: N_TASKS=1 (limits how many dataset tasks to run)
N_TASKS="${N_TASKS:-}"
MAX_ITERATIONS=200
TEMPERATURE=0.7

# --- Context condensation (LLM summarizer) ----------------------------------
# Set CONDENSER to "" (empty) to disable condensation entirely.
CONDENSER="${CONDENSER:-summarizer}"
# Trigger condensation when the conversation view exceeds this many events.
# SDK requires CONDENSER_KEEP_FIRST < CONDENSER_MAX_SIZE // 2.
CONDENSER_MAX_SIZE="${CONDENSER_MAX_SIZE:-120}"
# Always keep the first N events (system prompt, task message, initial tool
# calls) intact -- these usually contain the problem statement the agent
# refers back to throughout the trial.
CONDENSER_KEEP_FIRST="${CONDENSER_KEEP_FIRST:-4}"
# Optional token-based trigger. Pick ~75-80% of the agent model's effective
# context window. Set to "" to disable the token-based trigger.
CONDENSER_MAX_TOKENS="${CONDENSER_MAX_TOKENS:-120000}"
# Minimum fraction of events that must be condensed (SDK default: 0.1).
CONDENSER_MIN_PROGRESS="${CONDENSER_MIN_PROGRESS:-0.1}"
# Leave the next four empty to reuse the agent's LLM (cheapest, no extra
# credentials). Override CONDENSER_LLM_MODEL to summarize with a smaller /
# cheaper model served on the same endpoint.
CONDENSER_LLM_MODEL="${CONDENSER_LLM_MODEL:-}"
CONDENSER_LLM_BASE_URL="${CONDENSER_LLM_BASE_URL:-}"
CONDENSER_LLM_API_KEY="${CONDENSER_LLM_API_KEY:-}"
# Summaries should be deterministic; the SDK does not default this for you.
CONDENSER_LLM_TEMPERATURE="${CONDENSER_LLM_TEMPERATURE:-0.0}"

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
# Stable in-container path that the custom agent uses to locate the mounted runtime.
CONTAINER_RUNTIME_ROOT="/opt/custom-agent-runtime/oh-sdk"
# Default self-contained runtime image. Override with RUNTIME_SOURCE_IMAGE if needed.
# v0.3 = v0.2 + CONDENSER_CONFIG_JSON / LLMSummarizingCondenser wiring.
# v0.4 = v0.3 + usage_id="condenser" fix (otherwise SDK's llm_registry rejects
#        the second LLM with "Usage ID 'default' already exists in registry").
# v0.5 = v0.4 + PYTHONSAFEPATH=1 in the runtime python/pip wrappers so the task
#        workspace cwd (e.g. /testbed) no longer shadows embedded site-packages
#        (fixes psf__requests-* import failures).
# (Build/push: bash scripts/agent_runtimes/openhands-sdk/1.14.0/build-runtime-image.sh
#   --image docker.io/jierun/c-oh-sdk-${VERSION}:v0.X --push)
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

condenser_args=()
if [[ -n "${CONDENSER}" ]]; then
  condenser_args+=(
    --ak "condenser=$CONDENSER"
    --ak "condenser_max_size=$CONDENSER_MAX_SIZE"
    --ak "condenser_keep_first=$CONDENSER_KEEP_FIRST"
    --ak "condenser_minimum_progress=$CONDENSER_MIN_PROGRESS"
    --ak "condenser_llm_temperature=$CONDENSER_LLM_TEMPERATURE"
  )
  if [[ -n "${CONDENSER_MAX_TOKENS}" ]]; then
    condenser_args+=(--ak "condenser_max_tokens=$CONDENSER_MAX_TOKENS")
  fi
  if [[ -n "${CONDENSER_LLM_MODEL}" ]]; then
    condenser_args+=(--ak "condenser_llm_model=$CONDENSER_LLM_MODEL")
  fi
  if [[ -n "${CONDENSER_LLM_BASE_URL}" ]]; then
    condenser_args+=(--ak "condenser_llm_base_url=$CONDENSER_LLM_BASE_URL")
  fi
  if [[ -n "${CONDENSER_LLM_API_KEY}" ]]; then
    condenser_args+=(--ak "condenser_llm_api_key=$CONDENSER_LLM_API_KEY")
  fi
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
  "${condenser_args[@]}" \
  --ae "LLM_BASE_URL=$LLM_BASE_URL" \
  --ae "LLM_API_KEY=$LLM_API_KEY" \
  --ae "CUSTOM_AGENT_RUNTIME_ROOT=$CONTAINER_RUNTIME_ROOT" \
  --ae "CUSTOM_AGENT_PYTHON=$CONTAINER_RUNTIME_ROOT/bin/python"
