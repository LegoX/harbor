#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Build a distributable Docker image for the custom OpenCode runtime.

The image checks out the upstream OpenCode source tag pinned in manifest.json,
applies the local Harbor patch set, builds ABI-specific OpenCode launchers, and
packages glibc and musl Node runtimes behind POSIX sh-compatible wrappers.

Usage:
  bash scripts/agent_runtimes/opencode/1.14.22/build-runtime-image.sh \
    --image docker.io/yourname/c-oc-1.14.22:v0.2

  bash scripts/agent_runtimes/opencode/1.14.22/build-runtime-image.sh \
    --image docker.io/yourname/c-oc-1.14.22:v0.2 \
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
PATCH_PATH="${SCRIPT_DIR}/opencode-disable-openai-compatible-streaming.patch"
RUNTIME_SELECT_NODE_SOURCE="${SCRIPT_DIR}/runtime-select-node.sh"
RUNTIME_ENV_SOURCE="${SCRIPT_DIR}/runtime-env.sh"
NODE_WRAPPER_SOURCE="${SCRIPT_DIR}/node-wrapper.sh"
OPENCODE_WRAPPER_SOURCE="${SCRIPT_DIR}/opencode-wrapper.sh"
METADATA_SCRIPT_SOURCE="${SCRIPT_DIR}/write-runtime-metadata.py"
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
  "M_SOURCE_REF": manifest["source_ref"],
  "M_NODE_IMAGE": manifest["node_image"],
  "M_NODE_ALPINE_IMAGE": manifest.get("node_alpine_image", "node:22-alpine"),
  "M_NODE_ABIS": " ".join(manifest.get("node_abis", ["glibc"])),
  "M_BUN_VERSION": str(manifest["bun_version"]),
  "M_NPM_VERSION": str(manifest["npm_version"]),
  "M_CONTAINER_RUNTIME_ROOT": manifest["container_runtime_root"],
  "M_PACKAGE_NAME": manifest["package_name"],
  "M_BINARY_NAME": manifest["binary_name"],
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

for required_path in \
  "${PATCH_PATH}" \
  "${RUNTIME_SELECT_NODE_SOURCE}" \
  "${RUNTIME_ENV_SOURCE}" \
  "${NODE_WRAPPER_SOURCE}" \
  "${OPENCODE_WRAPPER_SOURCE}" \
  "${METADATA_SCRIPT_SOURCE}"; do
  if [[ ! -f "${required_path}" ]]; then
    echo "Required build input not found: ${required_path}" >&2
    exit 1
  fi
done

TMP_DIR="$(mktemp -d)"
BUILD_CONTEXT="${TMP_DIR}/context"
PATCH_CONTEXT_PATH="${BUILD_CONTEXT}/$(basename "${PATCH_PATH}")"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

mkdir -p "${BUILD_CONTEXT}"
cp "${PATCH_PATH}" "${PATCH_CONTEXT_PATH}"
cp "${RUNTIME_SELECT_NODE_SOURCE}" "${BUILD_CONTEXT}/runtime-select-node.sh"
cp "${RUNTIME_ENV_SOURCE}" "${BUILD_CONTEXT}/runtime-env.sh"
cp "${NODE_WRAPPER_SOURCE}" "${BUILD_CONTEXT}/node-wrapper.sh"
cp "${OPENCODE_WRAPPER_SOURCE}" "${BUILD_CONTEXT}/opencode-wrapper.sh"
cp "${METADATA_SCRIPT_SOURCE}" "${BUILD_CONTEXT}/write-runtime-metadata.py"

cat > "${BUILD_CONTEXT}/Dockerfile" <<EOF
FROM oven/bun:${M_BUN_VERSION} AS glibc-builder

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      ca-certificates \
      curl \
      patch \
      pkg-config \
      python3 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src/opencode

COPY $(basename "${PATCH_PATH}") /tmp/$(basename "${PATCH_PATH}")

RUN curl -fsSL "https://github.com/anomalyco/opencode/archive/refs/tags/${M_SOURCE_REF}.tar.gz" -o /tmp/opencode-src.tgz && \
    tar -xzf /tmp/opencode-src.tgz --strip-components=1 -C /src/opencode && \
    patch -p1 < /tmp/$(basename "${PATCH_PATH}")

ENV OPENCODE_VERSION=${M_RUNTIME_VERSION}
ENV OPENCODE_CHANNEL=latest

RUN bun install --frozen-lockfile
RUN ./packages/opencode/script/build.ts --single --skip-install --skip-embed-web-ui
RUN install -Dm755 \$(echo /src/opencode/packages/opencode/dist/*/bin/opencode) /tmp/opencode

FROM oven/bun:${M_BUN_VERSION}-alpine AS musl-builder

RUN apk add --no-cache bash build-base ca-certificates curl patch pkgconf python3

WORKDIR /src/opencode

COPY $(basename "${PATCH_PATH}") /tmp/$(basename "${PATCH_PATH}")

RUN curl -fsSL "https://github.com/anomalyco/opencode/archive/refs/tags/${M_SOURCE_REF}.tar.gz" -o /tmp/opencode-src.tgz && \
    tar -xzf /tmp/opencode-src.tgz --strip-components=1 -C /src/opencode && \
    patch -p1 < /tmp/$(basename "${PATCH_PATH}")

ENV OPENCODE_VERSION=${M_RUNTIME_VERSION}
ENV OPENCODE_CHANNEL=latest

RUN bun install --frozen-lockfile
RUN ./packages/opencode/script/build.ts --single --skip-install --skip-embed-web-ui
RUN install -Dm755 \$(echo /src/opencode/packages/opencode/dist/*/bin/opencode) /tmp/opencode

FROM ${M_NODE_ALPINE_IMAGE} AS musl-node-builder

RUN apk add --no-cache ca-certificates libgcc libstdc++
RUN mkdir -p /opt/musl-node-home/lib && \
    cp -a /usr/local/. /opt/musl-node-home/ && \
    for path in /lib/*.so* /usr/lib/*.so*; do \
      if [ -f "\$path" ]; then cp -aL "\$path" /opt/musl-node-home/lib/; fi; \
    done && \
    /opt/musl-node-home/bin/node --version >/dev/null

FROM ${M_NODE_IMAGE}

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash ca-certificates python3 && \
    rm -rf /var/lib/apt/lists/*

ENV HARBOR_RUNTIME_ROOT=${M_CONTAINER_RUNTIME_ROOT}
ENV HARBOR_RUNTIME_NODE_HOME_ROOT=\${HARBOR_RUNTIME_ROOT}/node-home
ENV HARBOR_RUNTIME_OPENCODE_HOME_ROOT=\${HARBOR_RUNTIME_ROOT}/opencode-home
ENV HARBOR_RUNTIME_NODE_ABIS="${M_NODE_ABIS}"

RUN mkdir -p \
      \${HARBOR_RUNTIME_ROOT}/bin \
      \${HARBOR_RUNTIME_NODE_HOME_ROOT} \
      \${HARBOR_RUNTIME_OPENCODE_HOME_ROOT}/glibc/bin \
      \${HARBOR_RUNTIME_OPENCODE_HOME_ROOT}/musl/bin

COPY runtime-select-node.sh \${HARBOR_RUNTIME_ROOT}/runtime-select-node.sh
COPY runtime-env.sh \${HARBOR_RUNTIME_ROOT}/runtime-env.sh
COPY node-wrapper.sh \${HARBOR_RUNTIME_ROOT}/bin/node
COPY opencode-wrapper.sh \${HARBOR_RUNTIME_ROOT}/bin/${M_BINARY_NAME}
COPY write-runtime-metadata.py /tmp/write-runtime-metadata.py
COPY --from=glibc-builder /tmp/opencode \${HARBOR_RUNTIME_OPENCODE_HOME_ROOT}/glibc/bin/${M_BINARY_NAME}
COPY --from=musl-builder /tmp/opencode \${HARBOR_RUNTIME_OPENCODE_HOME_ROOT}/musl/bin/${M_BINARY_NAME}
COPY --from=musl-node-builder /opt/musl-node-home/ \${HARBOR_RUNTIME_NODE_HOME_ROOT}/musl/

RUN chmod +x \${HARBOR_RUNTIME_ROOT}/runtime-env.sh && \
    chmod +x \${HARBOR_RUNTIME_ROOT}/bin/node && \
    chmod +x \${HARBOR_RUNTIME_ROOT}/bin/${M_BINARY_NAME} && \
    chmod +x \${HARBOR_RUNTIME_OPENCODE_HOME_ROOT}/glibc/bin/${M_BINARY_NAME} && \
    chmod +x \${HARBOR_RUNTIME_OPENCODE_HOME_ROOT}/musl/bin/${M_BINARY_NAME} && \
    cp -a /usr/local/. \${HARBOR_RUNTIME_NODE_HOME_ROOT}/glibc/

RUN bash -lc 'set -euo pipefail; \
    for abi in \$HARBOR_RUNTIME_NODE_ABIS; do \
      CUSTOM_AGENT_RUNTIME_ROOT="\$HARBOR_RUNTIME_ROOT"; \
      export CUSTOM_AGENT_RUNTIME_ROOT; \
      CUSTOM_AGENT_RUNTIME_NODE_ABI="\$abi"; \
      export CUSTOM_AGENT_RUNTIME_NODE_ABI; \
      . "\$HARBOR_RUNTIME_ROOT/runtime-env.sh"; \
      "\$HARBOR_RUNTIME_ROOT/bin/node" --version >/dev/null; \
      "\$HARBOR_RUNTIME_ROOT/bin/${M_BINARY_NAME}" --version >/dev/null; \
    done; \
    unset CUSTOM_AGENT_RUNTIME_ROOT CUSTOM_AGENT_RUNTIME_NODE_ABI'

RUN bash -lc 'set -euo pipefail; \
    CUSTOM_AGENT_RUNTIME_ROOT="\$HARBOR_RUNTIME_ROOT"; \
    export CUSTOM_AGENT_RUNTIME_ROOT; \
    . "\$HARBOR_RUNTIME_ROOT/runtime-env.sh"; \
  actual_npm_version="\$("\$HARBOR_RUNTIME_NODE_HOME_ROOT/glibc/bin/npm" --version)"; \
    RUNTIME_ROOT="\$HARBOR_RUNTIME_ROOT" \
    PACKAGE_NAME="${M_PACKAGE_NAME}" \
    PACKAGE_VERSION="${M_RUNTIME_VERSION}" \
  NPM_VERSION="\$actual_npm_version" \
    NODE_ABIS="\$HARBOR_RUNTIME_NODE_ABIS" \
    BINARY_NAME="${M_BINARY_NAME}" \
    python3 /tmp/write-runtime-metadata.py'

LABEL harbor.runtime.agent="${M_AGENT}" \
      harbor.runtime.version="${M_RUNTIME_VERSION}" \
      harbor.runtime.mode="mounted-node-cli"
ENV CUSTOM_AGENT_RUNTIME_ROOT=${M_CONTAINER_RUNTIME_ROOT}
ENV CUSTOM_AGENT_OPENCODE=${M_CONTAINER_RUNTIME_ROOT}/bin/${M_BINARY_NAME}
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

echo "Built OpenCode runtime image:"
echo "  image: ${IMAGE_NAME}"
echo "  source_ref: ${M_SOURCE_REF}"
echo "  bun: ${M_BUN_VERSION}"
echo "  npm: ${M_NPM_VERSION}"
echo "  node_abis: ${M_NODE_ABIS}"
echo "  package_version: ${M_RUNTIME_VERSION}"
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
echo "  bash ${REPO_ROOT}/scripts/run_benchmarks/run_sweb100_c-oc_glm5fp8.sh"
