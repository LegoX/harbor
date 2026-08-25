# Custom Agent Runtimes

This fork packages Claude Code, OpenCode, and OpenHands SDK independently from
benchmark task images. Harbor mounts a prebuilt, self-contained agent runtime
read-only into each task container, so a locally available runtime can begin
agent execution without reinstalling its dependencies for every task.

This separation shortens the path from container startup to inference and
prevents common startup failures caused by task-image Python, Node.js, native
library, or tool versions conflicting with agent requirements.

## Why Runtime Images

- A locally cached runtime skips repeated agent installation for every task,
  reducing agent setup time and time to inference.
- Agent dependencies stay separate from task-image dependencies, greatly
  reducing compatibility-related startup failures.
- One tested runtime can be reused across heterogeneous benchmark images while
  task images remain independent from agent implementation details.
- Explicit versions and recorded image digests provide reproducibility,
  rollback, and historical evaluation support. Tags alone are mutable.
- A fallback installation path remains available for development.

The first use may still need to build or pull the runtime image. Runtime
separation does not reduce that transfer time, and it cannot eliminate every
host architecture, kernel, Docker Compose, or native ABI incompatibility.

## Maintained Runtimes

| Agent | Versioned recipe | Harbor class |
| --- | --- | --- |
| OpenHands SDK 1.33.0 | `scripts/agent_runtimes/openhands-sdk/1.33.0/` | `harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK` |
| OpenCode 1.18.7 | `scripts/agent_runtimes/opencode/1.18.7/` | `harbor.agents.custom.opencode:CustomOpenCode` |
| Claude Code 2.1.118 | `scripts/agent_runtimes/claude-code/2.1.118/` | `harbor.agents.custom.claude_code:CustomClaudeCode` |

Older recipes remain available when they are required to reproduce an existing
evaluation.

## Runtime Image Mounts

Each recipe contains a `manifest.json` and `build-runtime-image.sh`. The common
packager installs a standalone runtime and produces a stable directory such as:

```text
/opt/custom-agent-runtime/<agent>/
├── bin/
├── runtime-env.sh
└── runtime/             # Optional agent-specific entrypoints or patches
```

Build an image with an explicit tag:

```bash
bash scripts/agent_runtimes/openhands-sdk/1.33.0/build-runtime-image.sh \
  --image docker.io/example/custom-openhands-sdk:1.33.0-v1
```

Add `--push` after authenticating to publish it. Do not overwrite a tag used by
an existing experiment; publish a new recipe directory for a new upstream
version and a new image revision for recipe-only changes.

Harbor accepts Docker Compose volume objects through `--mounts-json`, including
the `image` mount type:

```json
[
  {
    "type": "image",
    "source": "docker.io/example/custom-openhands-sdk:1.33.0-v1",
    "target": "/opt/custom-agent-runtime/oh-sdk",
    "read_only": true,
    "image": {"subpath": "opt/custom-agent-runtime/oh-sdk"}
  }
]
```

Image mounts require a Docker Compose version that supports `type: image` and
`image.subpath`.

## Common Execution Flow

1. A wrapper selects the custom class through `--agent-import-path`.
2. Harbor mounts the requested runtime image into the task container.
3. The custom agent validates the expected executable and `runtime-env.sh`.
4. If validation succeeds, the mounted executable is used.
5. Otherwise, the agent uses its supported fallback installation path or fails
   with an actionable compatibility error.
6. The agent preserves its native events, messages, tool calls, and tool results
   in its original trajectory format, while LiteLLM writes normalized model requests
   and responses into the same trial log directory. The two views can then be
   curated for SFT or used directly for trajectory analysis.

Changes to Harbor-side detection, environment merging, configuration, command
construction, or headers belong in `src/harbor/agents/custom/`. Changes to the
agent CLI/SDK, pinned dependencies, or version-specific patches belong in the
versioned runtime recipe and require rebuilding the image.

## Agent Differences

| Behavior | OpenHands SDK | OpenCode | Claude Code |
| --- | --- | --- | --- |
| Runtime executable | Standalone Python plus Harbor runner | Patched OpenCode CLI | Claude CLI |
| Main iteration limit | `max_iterations` | Agent CLI configuration | `max_turns` |
| Provider configuration | OpenAI-compatible SDK settings | Merged OpenCode provider JSON | Anthropic-compatible or Bedrock environment |
| Native trajectory | `trajectory.json` | OpenCode messages/events | Claude JSONL output |
| LiteLLM trajectory | `litellm-trajectory.jsonl` | `litellm-trajectory.jsonl` | `litellm-trajectory.jsonl` |
| Notable compatibility | Requires Python 3.12+ without a mounted runtime | Runtime patch can disable incompatible streaming | Supports proxy and AWS Bedrock variables |

## Direct Run Example

```bash
export LITELLM_MASTER_KEY="$(openssl rand -hex 32)"
export MOUNTS_JSON='[
  {
    "type": "image",
    "source": "docker.io/example/custom-openhands-sdk:1.33.0-v1",
    "target": "/opt/custom-agent-runtime/oh-sdk",
    "read_only": true,
    "image": {"subpath": "opt/custom-agent-runtime/oh-sdk"}
  }
]'

uv run harbor run \
  --dataset swebench-verified-100 \
  --agent-import-path harbor.agents.custom.openhands_sdk:CustomOpenHandsSDK \
  --model hosted_vllm/example-model \
  --mounts-json "$MOUNTS_JSON" \
  --ak version=1.33.0 \
  --ak max_iterations=200 \
  --ae LLM_BASE_URL=http://host.example:4001/v1 \
  --ae "LLM_API_KEY=$LITELLM_MASTER_KEY" \
  --ae CUSTOM_AGENT_RUNTIME_ROOT=/opt/custom-agent-runtime/oh-sdk
```

The benchmark wrappers build `MOUNTS_JSON` and choose the correct environment
variables automatically. Override `RUNTIME_SOURCE_IMAGE` to select another
published runtime without editing the wrapper.

## Troubleshooting

- If mounted runtime detection fails, verify the mount target, image subpath,
  executable permissions, and `runtime-env.sh`.
- If native libraries fail to load, rebuild the self-contained image; do not
  mix libraries from the task image into the runtime path.
- If per-trial LiteLLM trajectories are missing, confirm that the mounted
  runtime version preserves `x-trajectory-output-path`.
- If Docker rejects `type: image`, upgrade Docker Compose or use the documented
  fallback installation path.
