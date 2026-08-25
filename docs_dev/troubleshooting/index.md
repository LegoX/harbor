# Troubleshooting

Start with the narrowest failing layer:

1. Confirm the repository environment with `uv run harbor --help`.
2. Confirm Docker and Docker Compose versions.
3. Run a one-task oracle smoke test.
4. Check the LiteLLM readiness and model endpoints.
5. Inspect the trial's `agent/`, `verifier/`, and `artifacts/` directories.
6. Check mounted runtime detection before debugging agent behavior.

## Topics

- [uv cache space](./uv-cache.md)
- [Custom runtime and image-mount issues](../custom-agents.md#troubleshooting)
- [LiteLLM and trajectory diagnostics](../api-services.md#diagnostic-order)
- [Network policy boundaries](../network-policy-nohack.md#security-boundaries)

When reporting a problem, include the Harbor commit, Docker and Compose
versions, one redacted trial configuration, the relevant error log, and the
smallest command that reproduces the failure.
