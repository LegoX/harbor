#!/bin/bash
set -euo pipefail

# Resume a Harbor job without writing plaintext credentials back to config files.
# Masked agent env values are restored in memory by `harbor jobs resume` from
# same-named variables exported in this shell.

script_name="$(basename "$0")"

usage() {
  cat <<EOF
usage: $script_name [--n-concurrent N] <job_dir> [error_type ...]
   or: JOB_PATH=<job_dir> [N_CONCURRENT=N] $script_name [error_type ...]

Environment:
  <SECRET_NAME>=value      Restore a masked agent env value with the same name.
  ALLOW_MASKED_SECRETS=1  Continue intentionally if masked values remain.
  N_CONCURRENT=N           Override concurrency for this resume only.
  DRY_RUN=1                Print the command without starting the job.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --n-concurrent|-n)
      [[ -n "${2:-}" ]] || { echo "ERROR: $1 requires a value" >&2; exit 1; }
      N_CONCURRENT="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "ERROR: unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      if [[ -z "${JOB_PATH:-}" ]]; then
        JOB_PATH="$1"
        shift
      else
        break
      fi
      ;;
  esac
done

JOB_PATH="${JOB_PATH:-}"
[[ -n "$JOB_PATH" ]] || { usage; exit 1; }
[[ -d "$JOB_PATH" ]] || { echo "ERROR: job directory not found: $JOB_PATH" >&2; exit 1; }
[[ -f "$JOB_PATH/config.json" ]] || { echo "ERROR: config.json not found in: $JOB_PATH" >&2; exit 1; }

default_filters=(RuntimeError AgentTimeoutError NonZeroAgentExitCodeError AgentSetupTimeoutError CancelledError VerifierTimeoutError)
[[ $# -gt 0 ]] || set -- "${default_filters[@]}"

cmd=(uv run harbor jobs resume -p "$JOB_PATH")
if [[ -n "${N_CONCURRENT:-}" ]]; then
  [[ "${N_CONCURRENT}" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: N_CONCURRENT must be a positive integer" >&2; exit 1; }
  cmd+=(--n-concurrent "$N_CONCURRENT")
fi
for error_type in "$@"; do
  cmd+=(-f "$error_type")
done

printf '[resume] Command:'
printf ' %q' "${cmd[@]}"
printf '\n'

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "[resume] Dry run complete; no files were changed"
  exit 0
fi

"${cmd[@]}"
