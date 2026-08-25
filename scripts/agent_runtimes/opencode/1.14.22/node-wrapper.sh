#!/bin/sh
set -e

runtime_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
CUSTOM_AGENT_RUNTIME_ROOT=${CUSTOM_AGENT_RUNTIME_ROOT:-$runtime_root}
export CUSTOM_AGENT_RUNTIME_ROOT

. "$runtime_root/runtime-select-node.sh"
harbor_runtime_exec_node "$@"