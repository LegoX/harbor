"""Helpers for routing LiteLLM trajectory records without exposing host paths."""

from pathlib import Path


def trajectory_output_route(logs_dir: Path, filename: str) -> str:
    """Return a portable job/trial route for standard Harbor job directories.

    Non-standard log directories retain the legacy absolute filename so direct
    agent use remains compatible. The proxy must explicitly allow the parent
    root before it accepts such absolute paths.
    """
    resolved_logs_dir = logs_dir.resolve()
    if (
        resolved_logs_dir.name == "agent"
        and resolved_logs_dir.parent.parent.parent.name == "jobs"
    ):
        job_name = resolved_logs_dir.parent.parent.name
        trial_name = resolved_logs_dir.parent.name
        return f"{job_name}/{trial_name}"

    return str(resolved_logs_dir / filename)
