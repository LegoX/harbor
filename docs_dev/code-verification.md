# Code Verification

Run the repository checks after modifying code:

```bash
uv run ruff check --fix .
uv run ruff format .
uv run ty check
uv run pytest tests/unit/
```

Unit tests are the default verification scope. Run integration or runtime tests
only when a change affects external services, Docker runtime behavior that is
not covered by unit tests, or another integration-tested boundary.

## Targeted Tests for Fork Extensions

```bash
uv run pytest \
  tests/unit/test_custom_claude_code_agent.py \
  tests/unit/test_custom_opencode_agent.py \
  tests/unit/test_custom_openhands_sdk_agent.py \
  tests/unit/trial/test_network_policy.py \
  tests/unit/test_predict_patch_capture.py \
  tests/unit/test_trajectory_logger.py
```

Shell wrappers should also pass `bash -n`. Documentation changes should be
checked for broken relative links, stale filenames, machine-specific paths,
credentials, and private endpoints.

The job-analysis tests use that tool's directory as the import root:

```bash
cd scripts/job_analysis
uv run --project ../.. python -m pytest tests -q
```

## Project Conventions

- Use `Path.read_text()` and `Path.write_text()` for simple file I/O.
- Prefer `asyncio.TaskGroup` over `asyncio.gather`.
- Use debug logging unless information must be visible to the user at runtime.
- Add focused unit tests for configuration validation and failure behavior.
- Keep custom integrations in `src/harbor/agents/custom/` when upstream agent
  behavior should remain easy to compare and update.
