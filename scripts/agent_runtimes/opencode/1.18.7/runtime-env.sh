# POSIX sh file intended to be sourced by task containers.

CUSTOM_AGENT_RUNTIME_ROOT=${CUSTOM_AGENT_RUNTIME_ROOT:-/opt/custom-agent-runtime/opencode}
export CUSTOM_AGENT_RUNTIME_ROOT

if [ ! -f "$CUSTOM_AGENT_RUNTIME_ROOT/runtime-select-node.sh" ]; then
  echo "Runtime Node selector not found: $CUSTOM_AGENT_RUNTIME_ROOT/runtime-select-node.sh" >&2
  harbor_runtime_status=1
  return "$harbor_runtime_status" 2>/dev/null || exit "$harbor_runtime_status"
fi

. "$CUSTOM_AGENT_RUNTIME_ROOT/runtime-select-node.sh" || {
  harbor_runtime_status=$?
  return "$harbor_runtime_status" 2>/dev/null || exit "$harbor_runtime_status"
}
EMBEDDED_NODE_HOME=$(harbor_runtime_select_node_home) || {
  harbor_runtime_status=$?
  return "$harbor_runtime_status" 2>/dev/null || exit "$harbor_runtime_status"
}
OPENCODE_RUNTIME_BINARY=$(harbor_runtime_select_opencode_binary) || {
  harbor_runtime_status=$?
  return "$harbor_runtime_status" 2>/dev/null || exit "$harbor_runtime_status"
}
export EMBEDDED_NODE_HOME
export OPENCODE_RUNTIME_BINARY
export PATH="$CUSTOM_AGENT_RUNTIME_ROOT/bin:$EMBEDDED_NODE_HOME/bin${PATH:+:$PATH}"
export npm_config_update_notifier=false