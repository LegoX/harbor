# Custom Agent Runtime Recipes

This directory contains prebuilt, self-contained, and versioned runtime-image
recipes for the custom agents in `src/harbor/agents/custom/`. Mounting these
runtimes separately from task images avoids repeated per-task installation and
reduces dependency compatibility failures; versioning additionally supports
reproducibility and rollback.

## Maintained Versions

| Runtime | Version | Recipe |
| --- | --- | --- |
| OpenHands SDK | 1.33.0 | `openhands-sdk/1.33.0/` |
| OpenCode | 1.18.7 | `opencode/1.18.7/` |
| Claude Code | 2.1.118 | `claude-code/2.1.118/` |

Older recipes are retained for reproducibility.

## Layout

```text
scripts/agent_runtimes/
├── package-self-contained-runtime-image.sh
├── openhands-sdk/<version>/
│   ├── manifest.json
│   ├── build-runtime-image.sh
│   └── runtime/
├── opencode/<version>/
└── claude-code/<version>/
```

- `manifest.json` is the versioned source of truth for dependencies and paths.
- `build-runtime-image.sh` builds and optionally publishes a runtime image.
- `runtime/` contains only version-specific entrypoints or patches.
- `package-self-contained-runtime-image.sh` provides the common standalone
  runtime packaging implementation.

Use immutable digests for reported experiments. A unique tag is useful for
human identification but remains mutable; never replace a runtime tag still
used by an existing evaluation. Recipes may also depend on mutable base tags or
unchecked upstream archives, so record the final built-image digest.

See the [custom agent guide](../../docs_dev/custom-agents.md) for build, mount,
execution, fallback, and troubleshooting details.
