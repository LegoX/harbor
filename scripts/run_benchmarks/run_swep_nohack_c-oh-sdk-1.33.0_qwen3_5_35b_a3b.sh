#!/bin/bash
# SWE-bench Pro nohack run for OpenHands SDK 1.33.0 on Qwen3.5-35B-A3B.
# Agent egress is limited to the configured LiteLLM endpoint. Environment setup
# and verifier phases remain public so images can build and official Pro tests
# can resolve runtime dependencies.
#
# Start the proxy with:
#
#   LITELLM_PORT=4001 bash scripts/serve_llm/serve_litellm.sh
#
# Full benchmark smoke test:
#
#   NOHACK_GENERATE_LIMIT=1 N_TASKS=1 N_CONCURRENT=1 \
#     bash scripts/run_benchmarks/run_swep_nohack_c-oh-sdk-1.33.0_qwen3_5_35b_a3b.sh
#
# To use the registered 100-task subset instead:
#
#   SOURCE_DATASET=swebenchpro-100 \
#   DATASET=swebenchpro-100-nohack \
#     bash scripts/run_benchmarks/run_swep_nohack_c-oh-sdk-1.33.0_qwen3_5_35b_a3b.sh

set -euo pipefail

export DOCKER_BUILDKIT=0

# Stopping containers may disrupt another active job, so it is opt-in.
if [[ "${HARBOR_STOP_EXISTING_CONTAINERS:-0}" == "1" ]]; then
  running_harbor_containers=$(docker ps --format '{{.Names}}' | grep -E '__[a-z0-9]{7}-main-1$' || true)
  if [[ -n "$running_harbor_containers" ]]; then
    n_stale=$(echo "$running_harbor_containers" | wc -l)
    echo "Stopping ${n_stale} leftover harbor container(s)..."
    echo "$running_harbor_containers" | xargs docker stop 2>/dev/null || true
  fi
fi
if [[ "${HARBOR_PRUNE_DOCKER:-0}" == "1" ]]; then docker container prune -f && docker network prune -f; fi

SOURCE_DATASET="${SOURCE_DATASET:-swebenchpro}"
DATASET="${DATASET:-${SOURCE_DATASET}-nohack}"
AGENT=custom-openhands-sdk
AGENT_IMPORT_PATH=harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK
VERSION=1.33.0
PROVIDER=hosted_vllm
MODEL_NAME=Qwen3.5-35B-A3B
MAX_RETRIES=2
# Retry all real trial exceptions, including timeout and reward parsing errors.
RETRY_EXCLUDE_EXCEPTION="__harbor_retry_exclude_none__"
TIMEOUT_MULTIPLIER=1.0
# Pro tasks use larger, heterogeneous images and tests; start more conservatively.
N_CONCURRENT="${N_CONCURRENT:-24}"
OVERRIDE_MEMORY_MB="${OVERRIDE_MEMORY_MB:-8192}"
# Optional smoke: N_TASKS=1 limits how many registry tasks are run.
N_TASKS="${N_TASKS:-}"
# Optional task filter: whitespace-separated task names or glob patterns.
INCLUDE_TASK_NAMES="${INCLUDE_TASK_NAMES:-}"
MAX_ITERATIONS=200
TEMPERATURE=1.0

NOHACK_AUTO_GENERATE="${NOHACK_AUTO_GENERATE:-1}"
NOHACK_REFRESH="${NOHACK_REFRESH:-0}"
NOHACK_GENERATE_LIMIT="${NOHACK_GENERATE_LIMIT:-}"

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

ROOT_REGISTRY_PATH="${REPO_ROOT}/registry.json"
REGISTRY_DATASET_SLUG="${DATASET//-/_}"
REGISTRY_BASE_PATH="${REPO_ROOT}/scripts/git_ignore/hack_control/registry.${REGISTRY_DATASET_SLUG}.json"
NOHACK_GENERATOR="${REPO_ROOT}/scripts/misc/generate_swebench_verified_nohack.py"
NOHACK_SOURCE_TASK_DIR="${REPO_ROOT}/datasets/${SOURCE_DATASET}-local"
NOHACK_TASK_DIR="${REPO_ROOT}/datasets/${DATASET}"

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
    --source-dataset "$SOURCE_DATASET"
    --source-task-dir "$NOHACK_SOURCE_TASK_DIR"
    --task-dir "$NOHACK_TASK_DIR"
    --output-registry-path "$REGISTRY_PATH"
    --output-dataset-name "$DATASET"
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

LITELLM_PORT="${LITELLM_PORT:-4001}"
LLM_BASE_URL="http://${HOSTED_LITELLM_HOST}:${LITELLM_PORT}/v1"
LLM_API_KEY="${LITELLM_MASTER_KEY:?Set LITELLM_MASTER_KEY to the LiteLLM proxy master key}"
AGENT_ALLOWED_LLM_HOST="${AGENT_ALLOWED_LLM_HOST:-$HOSTED_LITELLM_HOST}"
# Keep the in-container path short; long mount targets can hit overlay2 limits.
CONTAINER_RUNTIME_ROOT="/oh-sdk"
DEFAULT_RUNTIME_SOURCE_IMAGE="docker.io/jierun/c-oh-sdk-${VERSION}:v0.1"
RUNTIME_SOURCE_IMAGE="${RUNTIME_SOURCE_IMAGE:-$DEFAULT_RUNTIME_SOURCE_IMAGE}"
RUNTIME_IMAGE_SUBPATH="${RUNTIME_IMAGE_SUBPATH:-/opt/custom-agent-runtime/oh-sdk}"

HOST_RUNTIME_DIR="${RUNTIME_HOST_DIR:-${REPO_ROOT}/.cache/harbor-runtime-oh-sdk-${VERSION}}"
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
  uv run python -c 'import json, os
mounts = [{
  "type": "bind",
  "source": os.environ["HOST_RUNTIME_DIR"],
  "target": os.environ["CONTAINER_RUNTIME_ROOT"],
  "read_only": True,
}]
print(json.dumps(mounts))'
)"

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

"${REPO_ROOT}/.venv/bin/harbor" run --dataset "$DATASET" \
  --registry-path "$REGISTRY_PATH" \
  --agent-import-path "$AGENT_IMPORT_PATH" \
  --job-name "$JOB_NAME" \
  --mounts-json "$MOUNTS_JSON" \
  --model "$PROVIDER/$MODEL_NAME" \
  --agent-extra-allowed-host "$AGENT_ALLOWED_LLM_HOST" \
  --n-concurrent "$N_CONCURRENT" \
  --override-memory-mb "$OVERRIDE_MEMORY_MB" \
  "${extra_args[@]}" \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --max-retries "$MAX_RETRIES" \
  --retry-exclude "$RETRY_EXCLUDE_EXCEPTION" \
  --ak "version=$VERSION" \
  --ak "temperature=$TEMPERATURE" \
  --ak "max_iterations=$MAX_ITERATIONS" \
  --ae "LLM_BASE_URL=$LLM_BASE_URL" \
  --ae "LLM_API_KEY=$LLM_API_KEY" \
  --ae 'LITELLM_EXTRA_BODY={"max_tokens":32768,"repetition_penalty":1.0}' \
  --ae "CUSTOM_AGENT_RUNTIME_ROOT=$CONTAINER_RUNTIME_ROOT" \
  --ae "CUSTOM_AGENT_PYTHON=$CONTAINER_RUNTIME_ROOT/bin/python"

# Run the same post-job trajectory analysis as the SWE-bench Verified nohack run.
JOB_DIR="${REPO_ROOT}/jobs/${JOB_NAME}"
ANALYSIS_DIR="${JOB_DIR}/analysis"
ANALYSIS_CONFIG="${ANALYSIS_DIR}/analysis_config.yaml"
JOB_ANALYSIS_DIR="${REPO_ROOT}/scripts/job_analysis"

mkdir -p "$ANALYSIS_DIR"

cat > "$ANALYSIS_CONFIG" <<EOF
data:
  log_dir: "${JOB_DIR}"
  trajectory_layout: "harbor_job"
  trajectory_subpath: "agent/litellm-trajectory.jsonl"
  gold_source: "harbor_dataset"
  dataset_dir: "${NOHACK_TASK_DIR}"
  trial_result_file: "result.json"
  trial_report_subpath: "verifier/report.json"
  max_iterations_default: ${MAX_ITERATIONS}

output:
  dir: "${ANALYSIS_DIR}"
  instances_jsonl: "instances.jsonl"
  report_json: "report.json"

analysis:
  include_resolved: true
  include_errors: true
  include_empty_patch: true

features:
  loop_threshold: 3
  premature_stop_threshold: 0.3
  tool_error_storm_threshold: 5

judge:
  enabled: false

hack_detector:
  enabled: true

task_analysis:
  enabled: true

instance_analysis:
  enabled: true
  out_subdir: "instance_analysis"

traj_analysis:
  enabled: true
  out_subdir: "traj_analysis"
  max_instances: null

skip_main_pipeline: false

scaffold: "openhands-sdk"
model: "${MODEL_NAME}"
taxonomy_version: "v1"
judge_version: "v1"
EOF

echo "Running error trajectory analysis..."
cd "$JOB_ANALYSIS_DIR"
python run.py --config "$ANALYSIS_CONFIG" --verbose
echo "Analysis results saved to: ${ANALYSIS_DIR}"
