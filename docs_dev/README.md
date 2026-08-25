# Harbor Fork Developer Guide

This guide documents the extensions in this repository relative to
[`harbor-framework/harbor@3396e6f`](https://github.com/harbor-framework/harbor/commit/3396e6f1f82b831108d26b0273d24f5424519f86).
It is written for developers who run evaluations, maintain custom agents, add
datasets, or analyze Harbor results.

## Feature Guide

| Feature | What this fork adds | Detailed guide |
| --- | --- | --- |
| Decoupled custom agent runtimes | Prebuilt, self-contained Claude Code, OpenCode, and OpenHands SDK runtimes that skip per-task installation and isolate agent dependencies from task images | [Custom agents](./custom-agents.md) |
| Phase-scoped networking | Independent environment, agent, and verifier network policies with Docker allowlist enforcement | [Network policy and no-hack evaluations](./network-policy-nohack.md) |
| Patch artifacts | Best-effort capture of agent changes against a pre-agent commit SHA, including automatic Git baseline initialization | [Running tasks](./run-tasks.md#automatic-patch-artifacts) |
| High-fidelity agent trajectories | Native agent events plus normalized per-call model requests and responses for SFT dataset preparation, trajectory analysis, and auditable evaluation; LiteLLM and vLLM launchers also support local inference | [Agent trajectories and local inference](./api-services.md) |
| Reproducible and resumable jobs | Tracked launch configurations, versioned runtime inputs, documented provenance, and config-drift-aware recovery | [Running and resuming jobs](./run-tasks.md); [reproducibility contract](./reproducibility.md) |
| Docker extensions | Image mounts, artifact mounts, build concurrency control, and output ownership repair | [Custom agents](./custom-agents.md#runtime-image-mounts) |
| Evaluation analysis | Failure attribution, task metadata, trajectory scoring, reward-hack signals, and runtime profiling | [Analysis tooling](./analysis-tooling.md) |
| Additional adapters | OpenSWE and multi-language SWE-rebench-V2 conversion and grading | [Adapters](./adapters.md) |

## Recommended Reading Order

1. [Quick start](./quick-start.md)
2. [Running tasks](./run-tasks.md)
3. [Custom agents](./custom-agents.md)
4. [Agent trajectories and local inference](./api-services.md)
5. [Network policy and no-hack evaluations](./network-policy-nohack.md)
6. [Analysis tooling](./analysis-tooling.md)
7. [Reproducible job workflows](./reproducibility.md)
8. [Code verification](./code-verification.md)
9. [Troubleshooting](./troubleshooting/index.md)

## Repository Areas Added or Extended by This Fork

```text
harbor/
├── src/harbor/agents/custom/    # Custom agent integrations
├── src/harbor/trial/            # Network policies and patch capture
├── adapters/openswe/            # OpenSWE adapter
├── adapters/swerebenchv2/       # SWE-rebench-V2 adapter
├── scripts/agent_runtimes/      # Versioned runtime image recipes
├── scripts/run_benchmarks/      # Benchmark launch and resume helpers
├── scripts/run_datasets/        # Dataset-oriented launch helpers
├── scripts/serve_llm/           # LiteLLM, vLLM, and trajectory tooling
├── scripts/job_analysis/        # Evaluation analysis pipeline
├── scripts/task_analysis/       # Task metadata classification
└── docs_dev/                    # This developer guide
```

The upstream Harbor documentation remains the source of truth for unchanged
framework behavior. This guide focuses only on fork-specific additions and
operational conventions.
