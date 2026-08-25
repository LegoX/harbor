# POSIX sh helper shared by runtime-env.sh and bin wrappers.

harbor_runtime_detect_musl() {
  for path in /lib/ld-musl-*.so* /usr/lib/libc.musl-*.so*; do
    if [ -f "$path" ]; then
      return 0
    fi
  done

  if command -v ldd >/dev/null 2>&1; then
    case "$(ldd --version 2>&1 || true)" in
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
    glibc_line=$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
    case "$glibc_line" in
      glibc\ *) version=${glibc_line#glibc } ;;
    esac
  fi

  if [ -z "$version" ] && command -v ldd >/dev/null 2>&1; then
    version=$(ldd --version 2>&1 | sed -n '1s/.* //p' || true)
  fi

  major=${version%%.*}
  rest=${version#*.}
  minor=${rest%%[^0-9]*}

  case "$major" in ''|*[!0-9]*) return 1 ;; esac
  case "$minor" in ''|*[!0-9]*) return 1 ;; esac

  if [ "$major" -lt 2 ]; then
    return 0
  fi
  if [ "$major" -eq 2 ] && [ "$minor" -lt 28 ]; then
    return 0
  fi

  return 1
}

harbor_runtime_selected_node_abi() {
  case "${CUSTOM_AGENT_RUNTIME_NODE_ABI:-}" in
    glibc|musl)
      printf '%s\n' "$CUSTOM_AGENT_RUNTIME_NODE_ABI"
      return 0
      ;;
    "")
      ;;
    *)
      echo "Unsupported CUSTOM_AGENT_RUNTIME_NODE_ABI: $CUSTOM_AGENT_RUNTIME_NODE_ABI" >&2
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
  root=${CUSTOM_AGENT_RUNTIME_ROOT:-/opt/custom-agent-runtime/opencode}
  abi=$(harbor_runtime_selected_node_abi) || return 1
  home="$root/node-home/$abi"
  HARBOR_RUNTIME_SELECTED_NODE_ABI="$abi"
  export HARBOR_RUNTIME_SELECTED_NODE_ABI

  if [ ! -x "$home/bin/node" ]; then
    echo "Selected $abi Node runtime is missing or not executable: $home" >&2
    return 1
  fi

  printf '%s\n' "$home"
}

harbor_runtime_select_opencode_binary() {
  root=${CUSTOM_AGENT_RUNTIME_ROOT:-/opt/custom-agent-runtime/opencode}
  abi=$(harbor_runtime_selected_node_abi) || return 1
  binary="$root/opencode-home/$abi/bin/opencode"

  if [ ! -x "$binary" ]; then
    echo "Selected $abi OpenCode runtime is missing or not executable: $binary" >&2
    return 1
  fi

  printf '%s\n' "$binary"
}

harbor_runtime_file_is_elf() {
  if ! command -v od >/dev/null 2>&1; then
    return 1
  fi

  magic=$(dd if="$1" bs=4 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
  [ "$magic" = "7f454c46" ]
}

harbor_runtime_exec_node() {
  home=$(harbor_runtime_select_node_home) || return 1
  selected_abi=$(harbor_runtime_selected_node_abi) || return 1
  node_executable="$home/bin/node"

  if [ "$selected_abi" = "musl" ]; then
    for loader in "$home"/lib/ld-musl-*.so*; do
      if [ -f "$loader" ]; then
        exec "$loader" --library-path "$home/lib" "$node_executable" "$@"
      fi
    done
  fi

  exec "$node_executable" "$@"
}

harbor_runtime_exec_opencode() {
  root=${CUSTOM_AGENT_RUNTIME_ROOT:-/opt/custom-agent-runtime/opencode}
  home=$(harbor_runtime_select_node_home) || return 1
  selected_abi=$(harbor_runtime_selected_node_abi) || return 1
  opencode_executable=$(harbor_runtime_select_opencode_binary) || return 1

  EMBEDDED_NODE_HOME="$home"
  export EMBEDDED_NODE_HOME
  export PATH="$root/bin:$EMBEDDED_NODE_HOME/bin${PATH:+:$PATH}"

  if [ "$selected_abi" = "musl" ] && harbor_runtime_file_is_elf "$opencode_executable"; then
    for loader in "$home"/lib/ld-musl-*.so*; do
      if [ -f "$loader" ]; then
        exec "$loader" --library-path "$home/lib" "$opencode_executable" "$@"
      fi
    done
  fi

  exec "$opencode_executable" "$@"
}