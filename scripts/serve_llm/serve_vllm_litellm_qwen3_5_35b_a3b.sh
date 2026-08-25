#!/bin/bash

set -euo pipefail

# ==============================================================================
# vLLM + LiteLLM Launcher
#
# Starts vLLM backend (OpenAI format) then LiteLLM proxy on top.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

source ~/miniconda3/etc/profile.d/conda.sh
conda activate vllm_0.18.1

# Cap OpenMP threads per worker. With TP=8 on a 96-core host, default would
# spawn 8*96=768 OMP threads competing for cache; 2 leaves 8*2=16 threads for
# tokenize/sampling and avoids oversubscription.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"

# ------------------------------------------------------------------------------
# Model settings
# ------------------------------------------------------------------------------
MODEL_PATH="${MODEL_PATH:?Set MODEL_PATH to the local Qwen3.5-35B-A3B model directory}"
MODEL_NAME="${MODEL_NAME:-Qwen3.5-35B-A3B}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-qwen3_coder}"
REASONING_PARSER="${REASONING_PARSER:-}"

# ------------------------------------------------------------------------------
# Server settings
# ------------------------------------------------------------------------------
HOST="${HOST:-0.0.0.0}"
VLLM_PORT="${VLLM_PORT:-8000}"
LITELLM_PORT="${LITELLM_PORT:-4001}"
API_KEY="${API_KEY:?Set API_KEY to a strong vLLM backend key}"
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to a strong proxy master key}"
export LITELLM_MASTER_KEY

# ------------------------------------------------------------------------------
# vLLM settings
# ------------------------------------------------------------------------------
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-262144}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-32}"

# ------------------------------------------------------------------------------
# LiteLLM settings
# ------------------------------------------------------------------------------
LITELLM_CONFIG="${LITELLM_CONFIG:-${SCRIPT_DIR}/litellm_config_qwen3_5_35b_a3b.yaml}"
VLLM_LOG_FOLDER="${SCRIPT_DIR}/vllm_log"
LITELLM_LOG_FOLDER="${SCRIPT_DIR}/litellm_log"
LITELLM_NUM_WORKERS="${LITELLM_NUM_WORKERS:-4}"
LITELLM_LOG_LEVEL="${LITELLM_LOG_LEVEL:-INFO}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "${VLLM_LOG_FOLDER}" "${LITELLM_LOG_FOLDER}"
VLLM_LOG_FILE="${VLLM_LOG_FOLDER}/${MODEL_NAME}_${TIMESTAMP}.log"
LITELLM_LOG_FILE="${LITELLM_LOG_FOLDER}/${MODEL_NAME}_${TIMESTAMP}.log"

# ------------------------------------------------------------------------------
# Pre-launch check. Killing unrelated listeners is opt-in.
# ------------------------------------------------------------------------------
for port in "${VLLM_PORT}" "${LITELLM_PORT}"; do
  existing_pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  if [[ -n "${existing_pid}" ]]; then
    if [[ "${ALLOW_PORT_PROCESS_KILL:-0}" != "1" ]]; then
      echo "ERROR: Port ${port} is occupied by PID ${existing_pid}." >&2
      echo "Stop it explicitly or set ALLOW_PORT_PROCESS_KILL=1 after verifying the PID." >&2
      exit 1
    fi
    echo "Port ${port} is occupied by PID ${existing_pid}; stopping it by request..."
    kill "${existing_pid}" 2>/dev/null
    sleep 2
    if kill -0 "${existing_pid}" 2>/dev/null; then
      echo "Process ${existing_pid} did not exit, sending SIGKILL..."
      kill -9 "${existing_pid}" 2>/dev/null
      sleep 1
    fi
    echo "Port ${port} freed."
  fi
done

# ------------------------------------------------------------------------------
# Cleanup on exit
# ------------------------------------------------------------------------------
cleanup() {
  echo ""
  echo "Stopping services..."
  [[ -n "${VLLM_PID:-}" ]] && kill "${VLLM_PID}" 2>/dev/null
  [[ -n "${LITELLM_PID:-}" ]] && kill "${LITELLM_PID}" 2>/dev/null
  wait 2>/dev/null
  echo "Stopped."
}
trap cleanup INT TERM

# ------------------------------------------------------------------------------
# Launch
# ------------------------------------------------------------------------------
echo "=========================================="
echo "Starting vLLM + LiteLLM for ${MODEL_NAME}"
echo "=========================================="
echo "Model path: ${MODEL_PATH}"
echo ""
echo "Endpoints:"
echo "  - vLLM (OpenAI format):       http://${HOST}:${VLLM_PORT}/v1"
echo "  - LiteLLM (Anthropic format): http://${HOST}:${LITELLM_PORT}/v1/messages"
echo "  - LiteLLM (OpenAI format):    http://${HOST}:${LITELLM_PORT}/v1/chat/completions"
echo ""
echo "API key: configured"
echo "=========================================="
echo ""

echo "[1/2] Starting vLLM backend..."
VLLM_EXTRA_ARGS=()
if [[ -n "${REASONING_PARSER}" ]]; then
  VLLM_EXTRA_ARGS+=(--reasoning-parser "${REASONING_PARSER}")
fi

vllm serve "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${VLLM_PORT}" \
    --api-key "${API_KEY}" \
    --served-model-name "${MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --trust-remote-code \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --enable-auto-tool-choice \
    --max-num-seqs "${MAX_NUM_SEQS}" \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    "${VLLM_EXTRA_ARGS[@]}" \
    --language-model-only \
    --gdn-prefill-backend triton \
    --dtype bfloat16 > >(tee "${VLLM_LOG_FILE}") 2>&1 &

VLLM_PID=$!

VLLM_TIMEOUT="${VLLM_TIMEOUT:-600}"
echo "Waiting for vLLM to be ready on port ${VLLM_PORT} (timeout: ${VLLM_TIMEOUT}s)..."
for i in $(seq 1 "${VLLM_TIMEOUT}"); do
  if curl -s --connect-timeout 2 "http://localhost:${VLLM_PORT}/health" >/dev/null 2>&1; then
    echo "vLLM is ready! (took ~${i}s)"
    break
  fi
  if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
    echo "ERROR: vLLM process died."
    exit 1
  fi
  sleep 1
done

if ! curl -s --connect-timeout 2 "http://localhost:${VLLM_PORT}/health" >/dev/null 2>&1; then
  echo "ERROR: vLLM did not become ready within ${VLLM_TIMEOUT}s."
  exit 1
fi

echo "[2/2] Starting LiteLLM proxy..."
LITELLM_LOG="${LITELLM_LOG_LEVEL}" litellm \
    --config "${LITELLM_CONFIG}" \
    --port "${LITELLM_PORT}" \
    --host "${HOST}" \
    --num_workers "${LITELLM_NUM_WORKERS}" 2>&1 | tee "${LITELLM_LOG_FILE}" &

LITELLM_PID=$!

echo "Waiting for LiteLLM to be ready on port ${LITELLM_PORT} (timeout: 60s)..."
for i in $(seq 1 60); do
  if curl -s --connect-timeout 2 -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" "http://localhost:${LITELLM_PORT}/health" >/dev/null 2>&1; then
    echo "LiteLLM is ready! (took ~${i}s)"
    break
  fi
  if ! kill -0 "${LITELLM_PID}" 2>/dev/null; then
    echo "ERROR: LiteLLM process died."
    exit 1
  fi
  sleep 1
done

if ! curl -s --connect-timeout 2 -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" "http://localhost:${LITELLM_PORT}/health" >/dev/null 2>&1; then
  echo "ERROR: LiteLLM did not become ready within 60s."
  exit 1
fi

# ------------------------------------------------------------------------------
# Reachability verification
# ------------------------------------------------------------------------------
echo ""
echo "=========================================="
echo "Reachability Test"
echo "=========================================="

VLLM_MODELS=$(curl -s --connect-timeout 5 -H "Authorization: Bearer ${API_KEY}" "http://localhost:${VLLM_PORT}/v1/models")
if echo "${VLLM_MODELS}" | grep -q "${MODEL_NAME}"; then
  echo "[PASS] vLLM /v1/models returns ${MODEL_NAME}"
else
  echo "[FAIL] vLLM /v1/models did not return expected model"
  echo "       Response: ${VLLM_MODELS}"
fi

LITELLM_MODELS=$(curl -s --connect-timeout 5 -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" "http://localhost:${LITELLM_PORT}/v1/models")
if echo "${LITELLM_MODELS}" | grep -q "model"; then
  echo "[PASS] LiteLLM /v1/models is reachable"
else
  echo "[FAIL] LiteLLM /v1/models did not respond correctly"
  echo "       Response: ${LITELLM_MODELS}"
fi

echo ""
echo "Both services started!"
echo "  - vLLM PID:    ${VLLM_PID}"
echo "  - LiteLLM PID: ${LITELLM_PID}"
echo "  - vLLM log:    ${VLLM_LOG_FILE}"
echo "  - LiteLLM log: ${LITELLM_LOG_FILE}"
echo ""

wait
