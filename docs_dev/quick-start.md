# Quick Start

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A working Docker installation with Docker Compose

If the default uv cache is on a constrained filesystem, see
[uv cache troubleshooting](./troubleshooting/uv-cache.md) before installing.

## Install the Development Environment

From the repository root:

```bash
uv sync --all-extras --dev
uv run harbor --help
```

`uv run` ensures that commands use the repository-local virtual environment.

## Run a Minimal Evaluation

Use an oracle smoke test to validate Harbor and Docker without an external LLM:

```bash
N_TASKS=1 N_CONCURRENT=1 \
  bash scripts/run_benchmarks/run_sweb100_oracle.sh
```

Wrappers do not prune Docker state by default. Destructive cleanup is available
only through the explicit safety overrides documented in
[Running tasks](./run-tasks.md#wrapper-safety).

## Choose the Next Guide

- Run or resume evaluations: [Running tasks](./run-tasks.md)
- Use mounted custom agents: [Custom agents](./custom-agents.md)
- Capture agent trajectories or start a local model gateway:
  [Agent trajectories and local inference](./api-services.md)
- Run an egress-restricted evaluation: [Network policy](./network-policy-nohack.md)
- Analyze completed jobs: [Analysis tooling](./analysis-tooling.md)
