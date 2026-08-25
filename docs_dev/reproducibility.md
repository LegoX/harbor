# Reproducible Job Workflows

This repository does not treat undocumented local scores or job directory names
as durable project documentation. Record results in an external experiment
system or an explicitly reviewed report, and keep this page as the required
reproducibility contract.

Here, reproducible means that the job inputs, code and runtime versions,
execution context, and result provenance are recorded well enough to rerun and
audit the evaluation. It does not imply bit-for-bit identical model output
across sampling runs, provider revisions, hardware, or infrastructure changes.

## Required Metadata

Every reported experiment should include:

- Harbor repository URL and full commit SHA;
- upstream baseline when the result is used to evaluate a fork feature;
- dataset name, version, split, selection procedure, and task count;
- agent class/import path and agent version;
- runtime image name and immutable digest (plus an optional human-readable tag);
- model name, provider/protocol, and relevant serving version;
- temperature, turn/iteration limits, timeouts, retries, and concurrency;
- network policy for environment, agent, and verifier phases;
- exact command or tracked wrapper/configuration;
- trial completion, infrastructure-error, and retry counts;
- metric definition and aggregation method;
- start time, duration, and relevant hardware characteristics.

An external runtime image namespace may be recorded when it identifies the
actual reproducibility artifact. Credentials, private endpoints, internal
hostnames, personal filesystem paths, and raw job directory names must not be
recorded.

The tracked runtime recipes improve repeatability but do not by themselves
guarantee bit-for-bit images: some recipes use mutable base-image tags or
upstream archives without a recorded checksum. For a published result, record
the final image digest and, where possible, pin base images and source archives
by digest or checksum.

## Feature Validation Expectations

| Feature | Minimum validation evidence |
| --- | --- |
| Mounted runtime | Runtime version/digest, mount config, detection log, and one successful trial |
| Network policy | Policy configuration plus allowed and blocked connection tests |
| Patch capture | Artifact content for tracked, untracked, committed, and no-`.git` task cases |
| Job resume | Completed-trial preservation, invalid-trial cleanup, and restored endpoint variables |
| Sticky routing | Stable task-to-alias assignment and cooldown/fallback behavior |
| Analysis tooling | Fixture/source provenance and deterministic output checks |
| Adapter | Conversion smoke test, oracle pass, no-op failure, and documented parity status |

## Record Template

```markdown
### <experiment name>

- Harbor commit:
- Upstream baseline:
- Dataset and selection:
- Agent and version:
- Runtime image/digest:
- Model and provider:
- Parameters:
- Network policy:
- Command or config:
- Completion/infra summary:
- Metric definition:
- Result artifact location:
- Notes and known limitations:
```

Do not fill missing provenance with guesses. Mark it unavailable and avoid
using that run as comparative evidence.
