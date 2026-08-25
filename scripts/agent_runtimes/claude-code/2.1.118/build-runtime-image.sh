#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Build a distributable Docker image for the custom Claude Code runtime.

The image installs @anthropic-ai/claude-code at the version pinned in
manifest.json, copies a node binary into the runtime root, and emits a
runtime-env.sh that makes the mounted CLI directly runnable inside Harbor task
containers.

Usage:
  bash scripts/agent_runtimes/claude-code/2.1.118/build-runtime-image.sh \
    --image docker.io/yourname/c-cc-2.1.118:v0.1

  bash scripts/agent_runtimes/claude-code/2.1.118/build-runtime-image.sh \
    --image docker.io/yourname/c-cc-2.1.118:v0.1 \
    --push

Options:
  --image NAME      Target image tag. Default: derived from manifest (harbor-…-runtime:version)
  --push            Push the image after building it.
  -h, --help        Show this help message.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
MANIFEST_PATH="${SCRIPT_DIR}/manifest.json"
HOST_PYTHON=(uv run --project "${REPO_ROOT}" python)

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_command docker
require_command uv

has_buildx() {
  docker buildx version >/dev/null 2>&1
}

load_manifest_exports() {
  # shellcheck disable=SC1090
  eval "$(
    MANIFEST_PATH="${MANIFEST_PATH}" "${HOST_PYTHON[@]}" - <<'PY'
import json
import os
import shlex
from pathlib import Path

manifest = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text())
exports = {
  "M_AGENT": manifest["agent"],
  "M_RUNTIME_VERSION": str(manifest["runtime_version"]),
  "M_NODE_IMAGE": manifest["node_image"],
  "M_NODE_ALPINE_IMAGE": manifest.get("node_alpine_image", "node:22-alpine"),
  "M_NODE_ABIS": " ".join(manifest.get("node_abis", ["glibc"])),
  "M_NPM_VERSION": str(manifest["npm_version"]),
  "M_CONTAINER_RUNTIME_ROOT": manifest["container_runtime_root"],
  "M_PACKAGE_NAME": manifest["package_name"],
  "M_PACKAGES_JSON": json.dumps(manifest["packages"], separators=(",", ":"), sort_keys=True),
}
default_image = f"harbor-{manifest['agent']}-runtime:{manifest['runtime_version']}"
for key, value in exports.items():
    print(f"export {key}={shlex.quote(value)}")
print(f"export M_DEFAULT_IMAGE={shlex.quote(default_image)}")
PY
  )"
}

load_manifest_exports

IMAGE_NAME="${M_DEFAULT_IMAGE}"
PUSH_IMAGE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE_NAME="${2:-}"
      shift 2
      ;;
    --push)
      PUSH_IMAGE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${IMAGE_NAME}" ]]; then
  echo "--image resolved to an empty name." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
BUILD_CONTEXT="${TMP_DIR}/context"
RUNTIME_ENV_PATH="${BUILD_CONTEXT}/runtime-env.sh"
RUNTIME_SELECT_NODE_PATH="${BUILD_CONTEXT}/runtime-select-node.sh"
NODE_WRAPPER_SCRIPT_PATH="${BUILD_CONTEXT}/node-wrapper.sh"
CLAUDE_WRAPPER_SCRIPT_PATH="${BUILD_CONTEXT}/claude-wrapper.sh"
METADATA_SCRIPT_PATH="${BUILD_CONTEXT}/write-runtime-metadata.py"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

mkdir -p "${BUILD_CONTEXT}"

cat > "${RUNTIME_SELECT_NODE_PATH}" <<EOF
# POSIX sh helper shared by runtime-env.sh and bin wrappers.

harbor_runtime_detect_musl() {
  for path in /lib/ld-musl-*.so* /usr/lib/libc.musl-*.so*; do
    if [ -f "\$path" ]; then
      return 0
    fi
  done

  if command -v ldd >/dev/null 2>&1; then
    case "\$(ldd --version 2>&1 || true)" in
      *musl*|*Musl*) return 0 ;;
    esac
  fi

  return 1
}

harbor_runtime_detect_old_glibc() {
  if harbor_runtime_detect_musl; then
    return 1
  fi

  version=""
  if command -v getconf >/dev/null 2>&1; then
    glibc_line=\$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
    case "\$glibc_line" in
      glibc\ *) version=\${glibc_line#glibc } ;;
    esac
  fi

  if [ -z "\$version" ] && command -v ldd >/dev/null 2>&1; then
    version=\$(ldd --version 2>&1 | sed -n '1s/.* //p' || true)
  fi

  major=\${version%%.*}
  rest=\${version#*.}
  minor=\${rest%%[^0-9]*}

  case "\$major" in ''|*[!0-9]*) return 1 ;; esac
  case "\$minor" in ''|*[!0-9]*) return 1 ;; esac

  if [ "\$major" -lt 2 ]; then
    return 0
  fi
  if [ "\$major" -eq 2 ] && [ "\$minor" -lt 28 ]; then
    return 0
  fi

  return 1
}

harbor_runtime_selected_node_abi() {
  case "\${CUSTOM_AGENT_RUNTIME_NODE_ABI:-}" in
    glibc|musl)
      printf '%s\n' "\$CUSTOM_AGENT_RUNTIME_NODE_ABI"
      return 0
      ;;
    "")
      ;;
    *)
      echo "Unsupported CUSTOM_AGENT_RUNTIME_NODE_ABI: \$CUSTOM_AGENT_RUNTIME_NODE_ABI" >&2
      return 1
      ;;
  esac

  if harbor_runtime_detect_musl; then
    printf '%s\n' musl
  elif harbor_runtime_detect_old_glibc; then
    printf '%s\n' musl
  else
    printf '%s\n' glibc
  fi
}

harbor_runtime_select_node_home() {
  root=\${CUSTOM_AGENT_RUNTIME_ROOT:-${M_CONTAINER_RUNTIME_ROOT}}
  abi=\$(harbor_runtime_selected_node_abi) || return 1
  home="\$root/node-home/\$abi"
  HARBOR_RUNTIME_SELECTED_NODE_ABI="\$abi"
  export HARBOR_RUNTIME_SELECTED_NODE_ABI

  if [ ! -x "\$home/bin/node" ]; then
    echo "Selected \$abi Node runtime is missing or not executable: \$home" >&2
    return 1
  fi

  printf '%s\n' "\$home"
}

harbor_runtime_resolve_node() {
  home=\$(harbor_runtime_select_node_home) || return 1
  printf '%s\n' "\$home/bin/node"
}

harbor_runtime_select_node_global() {
  root=\${CUSTOM_AGENT_RUNTIME_ROOT:-${M_CONTAINER_RUNTIME_ROOT}}
  abi=\$(harbor_runtime_selected_node_abi) || return 1
  global="\$root/node-global/\$abi"

  if [ ! -x "\$global/bin/claude" ]; then
    echo "Selected \$abi Claude Code runtime is missing or not executable: \$global" >&2
    return 1
  fi

  printf '%s\n' "\$global"
}

harbor_runtime_exec_node() {
  home=\$(harbor_runtime_select_node_home) || return 1
  selected_abi=\$(harbor_runtime_selected_node_abi) || return 1
  node_executable="\$home/bin/node"

  if [ "\$selected_abi" = "musl" ]; then
    for loader in "\$home"/lib/ld-musl-*.so*; do
      if [ -f "\$loader" ]; then
        exec "\$loader" --library-path "\$home/lib" "\$node_executable" "\$@"
      fi
    done
  fi

  exec "\$node_executable" "\$@"
}
EOF

cat > "${NODE_WRAPPER_SCRIPT_PATH}" <<'EOF'
#!/bin/sh
set -e

runtime_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
CUSTOM_AGENT_RUNTIME_ROOT=${CUSTOM_AGENT_RUNTIME_ROOT:-$runtime_root}
export CUSTOM_AGENT_RUNTIME_ROOT

. "$runtime_root/runtime-select-node.sh"
harbor_runtime_exec_node "$@"
EOF

cat > "${CLAUDE_WRAPPER_SCRIPT_PATH}" <<'EOF'
#!/bin/sh
set -e

runtime_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
CUSTOM_AGENT_RUNTIME_ROOT=${CUSTOM_AGENT_RUNTIME_ROOT:-$runtime_root}
export CUSTOM_AGENT_RUNTIME_ROOT

. "$runtime_root/runtime-select-node.sh"
EMBEDDED_NODE_HOME=$(harbor_runtime_select_node_home)
EMBEDDED_NODE_GLOBAL=$(harbor_runtime_select_node_global)
selected_abi=$(harbor_runtime_selected_node_abi)
export EMBEDDED_NODE_HOME
export EMBEDDED_NODE_GLOBAL
export PATH="$runtime_root/bin:$EMBEDDED_NODE_GLOBAL/bin:$EMBEDDED_NODE_HOME/bin${PATH:+:$PATH}"
export npm_config_prefix="$EMBEDDED_NODE_GLOBAL"
export npm_config_update_notifier=false

claude_executable="$EMBEDDED_NODE_GLOBAL/bin/claude"
if [ "$selected_abi" = "musl" ]; then
  for loader in "$EMBEDDED_NODE_HOME"/lib/ld-musl-*.so*; do
    if [ -f "$loader" ]; then
      exec "$loader" --library-path "$EMBEDDED_NODE_HOME/lib" "$claude_executable" "$@"
    fi
  done
fi

exec "$claude_executable" "$@"
EOF

cat > "${RUNTIME_ENV_PATH}" <<EOF
# POSIX sh file intended to be sourced by task containers.

CUSTOM_AGENT_RUNTIME_ROOT=\${CUSTOM_AGENT_RUNTIME_ROOT:-${M_CONTAINER_RUNTIME_ROOT}}
export CUSTOM_AGENT_RUNTIME_ROOT

if [ ! -f "\$CUSTOM_AGENT_RUNTIME_ROOT/runtime-select-node.sh" ]; then
  echo "Runtime Node selector not found: \$CUSTOM_AGENT_RUNTIME_ROOT/runtime-select-node.sh" >&2
  harbor_runtime_status=1
  return "\$harbor_runtime_status" 2>/dev/null || exit "\$harbor_runtime_status"
fi

. "\$CUSTOM_AGENT_RUNTIME_ROOT/runtime-select-node.sh" || {
  harbor_runtime_status=\$?
  return "\$harbor_runtime_status" 2>/dev/null || exit "\$harbor_runtime_status"
}
EMBEDDED_NODE_HOME=\$(harbor_runtime_select_node_home) || {
  harbor_runtime_status=\$?
  return "\$harbor_runtime_status" 2>/dev/null || exit "\$harbor_runtime_status"
}
EMBEDDED_NODE_GLOBAL=\$(harbor_runtime_select_node_global) || {
  harbor_runtime_status=\$?
  return "\$harbor_runtime_status" 2>/dev/null || exit "\$harbor_runtime_status"
}
export EMBEDDED_NODE_HOME
export EMBEDDED_NODE_GLOBAL
export PATH="\$CUSTOM_AGENT_RUNTIME_ROOT/bin:\$EMBEDDED_NODE_GLOBAL/bin:\$EMBEDDED_NODE_HOME/bin\${PATH:+:\$PATH}"
export npm_config_prefix="\$EMBEDDED_NODE_GLOBAL"
export npm_config_update_notifier=false
EOF

cat > "${METADATA_SCRIPT_PATH}" <<'EOF'
import json
import os
import subprocess
from pathlib import Path

runtime_root = Path(os.environ["RUNTIME_ROOT"])
package_name = os.environ["PACKAGE_NAME"]
package_version = os.environ["PACKAGE_VERSION"]
npm_version = os.environ["NPM_VERSION"]
node_abis = os.environ["NODE_ABIS"].split()

metadata = {
    "runtime_mode": "mounted-node-cli",
    "claude_executable": str(runtime_root / "bin" / "claude"),
    "node_executable": str(runtime_root / "bin" / "node"),
    "node_abis": node_abis,
    "node_homes": {
        abi: str(runtime_root / "node-home" / abi) for abi in node_abis
    },
    "node_globals": {
        abi: str(runtime_root / "node-global" / abi) for abi in node_abis
    },
    "runtime_env_script": str(runtime_root / "runtime-env.sh"),
    "packages": {
        "npm": npm_version,
        package_name: package_version,
    },
}

version_result = subprocess.run(
    [str(runtime_root / "bin" / "claude"), "--version"],
    check=True,
    capture_output=True,
    text=True,
    env={
        **os.environ,
        "CUSTOM_AGENT_RUNTIME_NODE_ABI": "glibc",
        "PATH": f"{runtime_root / 'bin'}:{runtime_root / 'node-global' / 'glibc' / 'bin'}:{os.environ.get('PATH', '')}",
    },
)
metadata["claude_version_output"] = version_result.stdout.strip()

(runtime_root / "runtime-metadata.json").write_text(json.dumps(metadata, indent=2))
EOF

cat > "${BUILD_CONTEXT}/Dockerfile" <<EOF
FROM ${M_NODE_ALPINE_IMAGE} AS musl-node-builder

RUN apk add --no-cache ca-certificates libgcc libstdc++
RUN npm install -g --prefix /opt/musl-node-global npm@${M_NPM_VERSION} && \\
    /opt/musl-node-global/bin/npm install -g --prefix /opt/musl-node-global ${M_PACKAGE_NAME}@${M_RUNTIME_VERSION} && \\
    /opt/musl-node-global/bin/claude --version >/dev/null
RUN mkdir -p /opt/musl-node-home/lib && \\
    cp -a /usr/local/. /opt/musl-node-home/ && \\
    for path in /lib/*.so* /usr/lib/*.so*; do \\
      if [ -f "\$path" ]; then cp -aL "\$path" /opt/musl-node-home/lib/; fi; \\
    done && \\
    /opt/musl-node-home/bin/node --version >/dev/null

FROM ${M_NODE_IMAGE}

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash ca-certificates python3 && \\
    rm -rf /var/lib/apt/lists/*

ENV HARBOR_RUNTIME_ROOT=${M_CONTAINER_RUNTIME_ROOT}
ENV HARBOR_NODE_GLOBAL_ROOT=\${HARBOR_RUNTIME_ROOT}/node-global
ENV HARBOR_RUNTIME_NODE_HOME_ROOT=\${HARBOR_RUNTIME_ROOT}/node-home
ENV HARBOR_RUNTIME_NODE_ABIS="${M_NODE_ABIS}"

RUN mkdir -p \\
      \${HARBOR_RUNTIME_ROOT}/bin \\
      \${HARBOR_RUNTIME_ROOT}/node-global \\
      \${HARBOR_RUNTIME_ROOT}/node-home

COPY runtime-select-node.sh \${HARBOR_RUNTIME_ROOT}/runtime-select-node.sh
COPY runtime-env.sh \${HARBOR_RUNTIME_ROOT}/runtime-env.sh
COPY node-wrapper.sh \${HARBOR_RUNTIME_ROOT}/bin/node
COPY claude-wrapper.sh \${HARBOR_RUNTIME_ROOT}/bin/claude
COPY write-runtime-metadata.py /tmp/write-runtime-metadata.py
COPY --from=musl-node-builder /opt/musl-node-home/ \${HARBOR_RUNTIME_NODE_HOME_ROOT}/musl/
COPY --from=musl-node-builder /opt/musl-node-global/ \${HARBOR_NODE_GLOBAL_ROOT}/musl/

RUN chmod +x \${HARBOR_RUNTIME_ROOT}/runtime-env.sh && \\
    chmod +x \${HARBOR_RUNTIME_ROOT}/bin/node && \\
    chmod +x \${HARBOR_RUNTIME_ROOT}/bin/claude && \\
    cp -a /usr/local/. \${HARBOR_RUNTIME_NODE_HOME_ROOT}/glibc/ && \\
    npm install -g --prefix "\${HARBOR_NODE_GLOBAL_ROOT}/glibc" npm@${M_NPM_VERSION} && \\
    "\${HARBOR_NODE_GLOBAL_ROOT}/glibc/bin/npm" install -g --prefix "\${HARBOR_NODE_GLOBAL_ROOT}/glibc" ${M_PACKAGE_NAME}@${M_RUNTIME_VERSION}

RUN bash -lc 'set -euo pipefail; \\
    for abi in \$HARBOR_RUNTIME_NODE_ABIS; do \\
      CUSTOM_AGENT_RUNTIME_NODE_ABI="\$abi"; \\
      export CUSTOM_AGENT_RUNTIME_NODE_ABI; \\
      . "\${HARBOR_RUNTIME_ROOT}/runtime-env.sh"; \\
      "\${HARBOR_RUNTIME_ROOT}/bin/node" --version >/dev/null; \\
      "\${HARBOR_RUNTIME_ROOT}/bin/claude" --version >/dev/null; \\
    done; \\
    unset CUSTOM_AGENT_RUNTIME_NODE_ABI'

RUN bash -lc 'set -euo pipefail; \\
    . "\${HARBOR_RUNTIME_ROOT}/runtime-env.sh"; \\
    RUNTIME_ROOT="\${HARBOR_RUNTIME_ROOT}" \\
    PACKAGE_NAME="${M_PACKAGE_NAME}" \\
    PACKAGE_VERSION="${M_RUNTIME_VERSION}" \\
    NPM_VERSION="${M_NPM_VERSION}" \\
    NODE_ABIS="\${HARBOR_RUNTIME_NODE_ABIS}" \\
    python3 /tmp/write-runtime-metadata.py'

LABEL harbor.runtime.agent="${M_AGENT}" \\
      harbor.runtime.version="${M_RUNTIME_VERSION}" \\
      harbor.runtime.mode="mounted-node-cli"
ENV CUSTOM_AGENT_RUNTIME_ROOT=${M_CONTAINER_RUNTIME_ROOT}
ENV CUSTOM_AGENT_CLAUDE=${M_CONTAINER_RUNTIME_ROOT}/bin/claude
WORKDIR \${HARBOR_RUNTIME_ROOT}
CMD ["bash"]
EOF

USED_BUILDER="legacy"
if has_buildx; then
  USED_BUILDER="buildx"
  build_args=(
    docker buildx build
    --tag "${IMAGE_NAME}"
  )
  if [[ ${PUSH_IMAGE} -eq 1 ]]; then
    build_args+=(--push)
  else
    build_args+=(--load)
  fi
  build_args+=("${BUILD_CONTEXT}")
  "${build_args[@]}"
else
  echo "docker buildx not found; falling back to legacy docker build." >&2
  docker build --tag "${IMAGE_NAME}" "${BUILD_CONTEXT}"
fi

echo "Built Claude Code runtime image:"
echo "  image: ${IMAGE_NAME}"
echo "  npm: ${M_NPM_VERSION}"
echo "  package: ${M_PACKAGE_NAME}@${M_RUNTIME_VERSION}"
echo "  node_abis: ${M_NODE_ABIS}"
echo "  musl_node_image: ${M_NODE_ALPINE_IMAGE}"
echo "  runtime_root: ${M_CONTAINER_RUNTIME_ROOT}"
echo "  builder: ${USED_BUILDER}"

if [[ ${PUSH_IMAGE} -eq 1 && "${USED_BUILDER}" == "legacy" ]]; then
  docker push "${IMAGE_NAME}"
  echo "Pushed runtime image:"
  echo "  image: ${IMAGE_NAME}"
fi

echo
echo "Next: run a benchmark with an image mount, for example:"
echo "  export RUNTIME_SOURCE_IMAGE=${IMAGE_NAME}"
echo "  bash ${REPO_ROOT}/scripts/run_benchmarks/run_sweb100_c-cc_glm5fp8.sh"
