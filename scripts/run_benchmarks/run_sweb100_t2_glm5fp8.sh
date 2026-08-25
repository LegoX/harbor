#!/bin/bash
set -euo pipefail

if [[ "${HARBOR_PRUNE_DOCKER:-0}" == "1" ]]; then docker container prune -f && docker network prune -f; fi

DATASET=swebench-verified-100
AGENT=terminus-2
MODEL_NAME=GLM-5-FP8
PROVIDER=hosted_vllm
MAX_TURN=200
TEMPERATURE=0.7
INTERLEAVED_THINKING=true
MAX_RETRIES=2
TIMEOUT_MULTIPLIER=2
N_CONCURRENT="${N_CONCURRENT:-6}"
MODEL_INFO='{"max_input_tokens":98304,"max_output_tokens":32768,"input_cost_per_token":0.0000021,"output_cost_per_token":0.0000084}'

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REGISTRY_PATH="${SCRIPT_DIR}/../../registry.json"

if [[ "$(uname)" == "Darwin" ]]; then
   HOSTED_LITELLM_HOST="host.docker.internal"
else
   HOSTED_LITELLM_HOST="$(hostname -I | awk '{print $1}')"
fi

HOSTED_VLLM_HOST="http://${HOSTED_LITELLM_HOST}:4000/v1"
HOSTED_VLLM_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
export HOSTED_VLLM_API_KEY

# DATASET-AGENT-MODEL_NAME-timestamp
JOB_NAME="${DATASET}-${AGENT}-${MODEL_NAME}-$(date +%Y%m%d%H%M%S)"

uv run harbor run --dataset "$DATASET" \
  --registry-path "$REGISTRY_PATH" \
  --agent "$AGENT" \
  --model "$PROVIDER/$MODEL_NAME" \
  --job-name "$JOB_NAME" \
  --n-concurrent "$N_CONCURRENT" \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --max-retries "$MAX_RETRIES" \
  --ak "max_turns=$MAX_TURN" \
  --ak "temperature=$TEMPERATURE" \
  --ak "api_base=$HOSTED_VLLM_HOST" \
  --ak "interleaved_thinking=$INTERLEAVED_THINKING" \
  --ak "model_info=$MODEL_INFO"
