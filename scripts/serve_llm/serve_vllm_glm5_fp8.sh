#!/bin/bash

# Prerequisites:
#   conda create -n vllm_0.19.1_tf_5.6.2 python=3.13
#   conda activate vllm_0.19.1_tf_5.6.2
#   pip install vllm==0.19.1 transformers==5.6.2

#
# Test:
#   curl http://localhost:8000/v1/chat/completions \
#     -H "Content-Type: application/json" \
#     -H "Authorization: Bearer dummy-key-cf" \
#     -d '{
#         "model": "GLM-5-FP8",
#         "messages": [
#             {"role": "system", "content": [{"type": "text", "text": "You are a helpful assistant."}]},
#             {"role": "user", "content": [{"type": "text", "text": "Who won the world series in 2020?"}]}
#         ]
#     }'


# vLLM Deployment Script
cd /data/code/serve_llm
source /data/miniconda3/etc/profile.d/conda.sh
conda activate vllm_glm5


MODEL_PATH=/data/models/GLM-5-FP8
MODEL_NAME=GLM-5-FP8
TOOL_CALL_PARSER=glm47 
REASONING_PARSER=glm45
MAX_NUM_SEQS=6

# Server configuration
HOST="127.0.0.1"
VLLM_PORT=8000                  # vLLM backend port (OpenAI format)
API_KEY="dummy-key"

# vLLM configuration
TENSOR_PARALLEL_SIZE=8          # Adjust based on available GPUs
MAX_MODEL_LEN=131072            # Maximum context length
GPU_MEMORY_UTILIZATION=0.86      # GPU memory utilization ratio
NUM_SPECULATIVE_TOKENS=3

cleanup() {
  echo ""
  echo "Stopping vLLM..."
  [[ -n "${VLLM_PID:-}" ]] && kill "${VLLM_PID}" 2>/dev/null
  wait 2>/dev/null
  echo "Stopped."
}
trap cleanup INT TERM

echo "=========================================="
echo "Starting vLLM for ${MODEL_NAME}"
echo "=========================================="
echo "Model path: ${MODEL_PATH}"
echo ""
echo "Endpoint:"
echo "  - vLLM (OpenAI format): http://${HOST}:${VLLM_PORT}/v1"
echo ""
echo "API key: ${API_KEY}"
echo "=========================================="
echo ""

# Start vLLM
echo "Starting vLLM backend..."
# export SAFETENSORS_FAST_GPU=1 # only for hf models with safetensors format, for local models, we use the default setting
# Export so tensor-parallel worker processes also inherit it
export SAFETENSORS_FAST_GPU=1
export OMP_NUM_THREADS=2 # Limit CPU threads for better performance
vllm serve "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${VLLM_PORT}" \
    --api-key "${API_KEY}" \
    --served-model-name "${MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --trust-remote-code \
    --speculative-config.method mtp \
    --speculative-config.num_speculative_tokens "${NUM_SPECULATIVE_TOKENS}" \
    --enable-auto-tool-choice \
    --max-num-seqs "${MAX_NUM_SEQS}" \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    --reasoning-parser "${REASONING_PARSER}" &

VLLM_PID=$!

echo ""
echo "vLLM started!"
echo "  - vLLM PID: ${VLLM_PID}"
echo ""
echo "Press Ctrl+C to stop the server."

# Keep script running
wait

