# Development and Experiment Scripts

This directory contains development utilities, reproducible experiment
wrappers, and local service helpers. Script interfaces are less stable than the
Harbor CLI; review the script header and use a smoke-test configuration first.

## Directories

- `agent_runtimes/`: versioned custom-agent runtime image recipes. See
  [custom agent documentation](../docs_dev/custom-agents.md).
- `run_benchmarks/`: benchmark wrappers and the `resume_job.sh` helper. See
  [running tasks](../docs_dev/run-tasks.md).
- `run_datasets/`: wrappers for prepared local datasets.
- `serve_llm/`: LiteLLM, vLLM, sticky routing, and trajectory logging. See
  [LLM services and tracing](../docs_dev/api-services.md).
- `job_analysis/`: failure attribution and trajectory analysis pipeline. See
  [analysis tooling](../docs_dev/analysis-tooling.md).
- `task_analysis/`: task metadata tagging and classification.
- `misc/`: dataset hardening, profiling, and one-purpose utilities.

## Safety

- Some wrappers prune stopped Docker containers and unused networks.
- Package publication scripts require explicit registry credentials and publish
  externally.
- Registry synchronization scripts change remote state; use their dry-run mode
  first.
- Analysis inputs and model trajectories may contain sensitive prompts, source
  code, endpoints, or credentials. Review generated reports before sharing.

Machine-specific model paths, service endpoints, credentials, dataset paths,
and job paths must be supplied through environment variables or command-line
arguments rather than committed as defaults.
