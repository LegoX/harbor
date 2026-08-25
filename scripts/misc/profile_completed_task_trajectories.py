#!/usr/bin/env python3
"""Profile completed Harbor task trajectories for API vs local bottlenecks.

The script reads a Harbor job directory and analyzes tasks that already have
``result.json``.  It combines Harbor phase timestamps with LiteLLM trajectory
records to estimate where time is spent:

* environment setup / agent setup / verifier wall time from ``result.json``
* API request durations from ``agent/litellm-trajectory.jsonl``
* non-API agent time as ``agent_execution_wall - merged_api_interval_wall``

Example:
    python scripts/misc/profile_completed_task_trajectories.py \
      /path/to/harbor/jobs/<job-name> \
      --output-dir scripts/misc/trajectory_profile_outputs/<profile-name>
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "trajectory_profile_outputs"

REQUEST_TIME_RE = re.compile(r'"request_time"\s*:\s*(\d+)')
DURATION_MS_RE = re.compile(r'"duration_ms"\s*:\s*(\d+)')
SUCCESS_RE = re.compile(r'"success"\s*:\s*(true|false)')
PROMPT_TOKENS_RE = re.compile(r'"prompt_tokens"\s*:\s*(\d+)')
COMPLETION_TOKENS_RE = re.compile(r'"completion_tokens"\s*:\s*(\d+)')


@dataclass
class ApiCall:
    start_ms: int
    end_ms: int
    duration_ms: int
    success: bool | None
    prompt_tokens: int
    completion_tokens: int


@dataclass
class TaskProfile:
    trial_name: str
    task_name: str
    reward: float | None
    exception_type: str | None
    total_wall_s: float
    env_setup_s: float
    agent_setup_s: float
    agent_execution_s: float
    verifier_s: float
    api_calls: int
    api_failures: int
    api_sum_s: float
    api_merged_s: float
    api_gap_s: float
    pre_first_api_gap_s: float
    between_api_gap_s: float
    post_last_api_gap_s: float
    api_coverage: float
    prompt_tokens: int
    completion_tokens: int
    trajectory_bytes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "job_dir",
        type=Path,
        help="Harbor job directory to profile.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Directory for task_profile.csv, api_minute_profile.csv, and report.md. "
            "Defaults to scripts/misc/trajectory_profile_outputs/<job-name>."
        ),
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        help="Deprecated alias for --output-dir.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of slow / gap-heavy tasks to print.",
    )
    parser.add_argument(
        "--include-running",
        action="store_true",
        help="Also parse trajectory files for trials without result.json in global API timeline.",
    )
    return parser.parse_args()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def duration_s(section: dict | None) -> float:
    if not isinstance(section, dict):
        return 0.0
    started = parse_time(section.get("started_at"))
    finished = parse_time(section.get("finished_at"))
    if not started or not finished:
        return 0.0
    return max((finished - started).total_seconds(), 0.0)


def epoch_ms(value: str | None) -> int | None:
    dt = parse_time(value)
    if not dt:
        return None
    return int(dt.timestamp() * 1000)


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def fmt_s(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.2f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.1f}s"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def parse_int_matches(pattern: re.Pattern[str], line: str) -> list[int]:
    return [int(match.group(1)) for match in pattern.finditer(line)]


def parse_api_call(line: str) -> ApiCall | None:
    request_time_match = REQUEST_TIME_RE.search(line)
    duration_match = DURATION_MS_RE.search(line)
    if not request_time_match or not duration_match:
        return None

    end_ms = int(request_time_match.group(1))
    duration_ms_value = int(duration_match.group(1))
    start_ms = end_ms - duration_ms_value

    success_match = SUCCESS_RE.search(line)
    success = None
    if success_match:
        success = success_match.group(1) == "true"

    prompt_tokens = max(parse_int_matches(PROMPT_TOKENS_RE, line) or [0])
    completion_tokens = max(parse_int_matches(COMPLETION_TOKENS_RE, line) or [0])

    return ApiCall(
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=duration_ms_value,
        success=success,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def read_api_calls(path: Path) -> list[ApiCall]:
    if not path.exists():
        return []

    calls: list[ApiCall] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            call = parse_api_call(line)
            if call:
                calls.append(call)
    return calls


def merged_intervals_ms(
    calls: Iterable[ApiCall],
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> list[tuple[int, int]]:
    intervals = sorted((call.start_ms, call.end_ms) for call in calls)
    if window_start_ms is not None or window_end_ms is not None:
        clipped_intervals = []
        for start, end in intervals:
            if window_start_ms is not None:
                start = max(start, window_start_ms)
            if window_end_ms is not None:
                end = min(end, window_end_ms)
            if end > start:
                clipped_intervals.append((start, end))
        intervals = clipped_intervals

    if not intervals:
        return []

    merged = []
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def merged_interval_ms(calls: Iterable[ApiCall]) -> int:
    return sum(end - start for start, end in merged_intervals_ms(calls))


def gap_breakdown_ms(
    merged_intervals: list[tuple[int, int]],
    window_start_ms: int | None,
    window_end_ms: int | None,
) -> tuple[int, int, int]:
    if (
        window_start_ms is None
        or window_end_ms is None
        or window_end_ms <= window_start_ms
    ):
        return 0, 0, 0
    if not merged_intervals:
        return window_end_ms - window_start_ms, 0, 0

    pre_gap = max(merged_intervals[0][0] - window_start_ms, 0)
    post_gap = max(window_end_ms - merged_intervals[-1][1], 0)
    between_gap = 0
    previous_end = merged_intervals[0][1]
    for start, end in merged_intervals[1:]:
        between_gap += max(start - previous_end, 0)
        previous_end = end
    return pre_gap, between_gap, post_gap


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_job_config(job_dir: Path) -> dict:
    config_path = job_dir / "config.json"
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def profile_completed_trial(trial_dir: Path) -> TaskProfile | None:
    result_path = trial_dir / "result.json"
    if not result_path.exists():
        return None

    result = load_result(result_path)
    calls = read_api_calls(trial_dir / "agent" / "litellm-trajectory.jsonl")
    api_sum_s = sum(call.duration_ms for call in calls) / 1000
    agent_execution = result.get("agent_execution")
    agent_start_ms = (
        epoch_ms(agent_execution.get("started_at"))
        if isinstance(agent_execution, dict)
        else None
    )
    agent_end_ms = (
        epoch_ms(agent_execution.get("finished_at"))
        if isinstance(agent_execution, dict)
        else None
    )
    merged_intervals = merged_intervals_ms(calls, agent_start_ms, agent_end_ms)
    api_merged_s = sum(end - start for start, end in merged_intervals) / 1000
    pre_first_api_gap_ms, between_api_gap_ms, post_last_api_gap_ms = gap_breakdown_ms(
        merged_intervals,
        agent_start_ms,
        agent_end_ms,
    )
    agent_execution_s = duration_s(result.get("agent_execution"))
    api_gap_s = max(agent_execution_s - api_merged_s, 0.0)
    api_coverage = api_merged_s / agent_execution_s if agent_execution_s > 0 else 0.0

    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else {}
    exception_info = result.get("exception_info")

    trajectory_path = trial_dir / "agent" / "litellm-trajectory.jsonl"

    return TaskProfile(
        trial_name=result.get("trial_name") or trial_dir.name,
        task_name=result.get("task_name") or trial_dir.name,
        reward=(rewards or {}).get("reward") if isinstance(rewards, dict) else None,
        exception_type=(exception_info or {}).get("exception_type")
        if isinstance(exception_info, dict)
        else None,
        total_wall_s=duration_s(
            {
                "started_at": result.get("started_at"),
                "finished_at": result.get("finished_at"),
            }
        ),
        env_setup_s=duration_s(result.get("environment_setup")),
        agent_setup_s=duration_s(result.get("agent_setup")),
        agent_execution_s=agent_execution_s,
        verifier_s=duration_s(result.get("verifier")),
        api_calls=len(calls),
        api_failures=sum(1 for call in calls if call.success is False),
        api_sum_s=api_sum_s,
        api_merged_s=api_merged_s,
        api_gap_s=api_gap_s,
        pre_first_api_gap_s=pre_first_api_gap_ms / 1000,
        between_api_gap_s=between_api_gap_ms / 1000,
        post_last_api_gap_s=post_last_api_gap_ms / 1000,
        api_coverage=api_coverage,
        prompt_tokens=sum(call.prompt_tokens for call in calls),
        completion_tokens=sum(call.completion_tokens for call in calls),
        trajectory_bytes=trajectory_path.stat().st_size
        if trajectory_path.exists()
        else 0,
    )


def summarize_metric(name: str, values: list[float], unit: str = "s") -> str:
    if not values:
        return f"{name:24s} n=0"
    formatter = fmt_s if unit == "s" else (lambda x: f"{x:.2f}")
    return (
        f"{name:24s} "
        f"mean={formatter(statistics.fmean(values))} "
        f"p50={formatter(quantile(values, 0.50))} "
        f"p90={formatter(quantile(values, 0.90))} "
        f"p95={formatter(quantile(values, 0.95))} "
        f"max={formatter(max(values))}"
    )


def metric_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0, "p50": 0, "p90": 0, "p95": 0, "max": 0}
    return {
        "mean": statistics.fmean(values),
        "p50": quantile(values, 0.50),
        "p90": quantile(values, 0.90),
        "p95": quantile(values, 0.95),
        "max": max(values),
    }


def markdown_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def print_summary(
    profiles: list[TaskProfile], all_calls: list[ApiCall], top: int
) -> None:
    if not profiles:
        print("No completed tasks found.")
        return

    print(f"completed_tasks: {len(profiles)}")
    print(
        f"tasks_with_api_calls: {sum(1 for profile in profiles if profile.api_calls)}"
    )
    print(f"total_api_calls: {sum(profile.api_calls for profile in profiles)}")
    print(f"total_api_failures: {sum(profile.api_failures for profile in profiles)}")
    print(
        f"total_prompt_tokens: {sum(profile.prompt_tokens for profile in profiles):,}"
    )
    print(
        f"total_completion_tokens: {sum(profile.completion_tokens for profile in profiles):,}"
    )
    print()

    print("Phase wall time per completed task:")
    print(summarize_metric("total_wall", [p.total_wall_s for p in profiles]))
    print(summarize_metric("env_setup", [p.env_setup_s for p in profiles]))
    print(summarize_metric("agent_setup", [p.agent_setup_s for p in profiles]))
    print(summarize_metric("agent_execution", [p.agent_execution_s for p in profiles]))
    print(summarize_metric("verifier", [p.verifier_s for p in profiles]))
    print()

    print("API vs local/tool time inside agent_execution:")
    print(summarize_metric("api_call_sum", [p.api_sum_s for p in profiles]))
    print(summarize_metric("api_merged_busy", [p.api_merged_s for p in profiles]))
    print(summarize_metric("non_api_gap", [p.api_gap_s for p in profiles]))
    print(
        summarize_metric(
            "api_coverage",
            [p.api_coverage for p in profiles if p.agent_execution_s > 0],
            unit="ratio",
        )
    )
    total_agent = sum(p.agent_execution_s for p in profiles)
    total_api_merged = sum(p.api_merged_s for p in profiles)
    print(
        f"weighted_api_coverage: {fmt_pct(total_api_merged / total_agent if total_agent else 0)}"
    )
    print()

    if all_calls:
        first_ms = min(call.start_ms for call in all_calls)
        last_ms = max(call.end_ms for call in all_calls)
        elapsed_s = max((last_ms - first_ms) / 1000, 1e-9)
        api_sum_s = sum(call.duration_ms for call in all_calls) / 1000
        print("Global API request concurrency:")
        print(f"api_window: {fmt_s(elapsed_s)}")
        print(f"api_sum_duration: {fmt_s(api_sum_s)}")
        print(f"avg_api_requests_in_flight: {api_sum_s / elapsed_s:.2f}")
        print(
            "Interpretation: if avg_api_requests_in_flight is far below backend capacity while "
            "non_api_gap is high, the bottleneck is likely local/tool/Docker I/O rather than GPU."
        )
        print()

    print(f"Top {top} tasks by non-API gap:")
    for profile in sorted(profiles, key=lambda p: p.api_gap_s, reverse=True)[:top]:
        print(
            f"{profile.trial_name} gap={fmt_s(profile.api_gap_s)} "
            f"agent={fmt_s(profile.agent_execution_s)} api_busy={fmt_s(profile.api_merged_s)} "
            f"coverage={fmt_pct(profile.api_coverage)} calls={profile.api_calls} "
            f"exception={profile.exception_type or '-'}"
        )
    print()

    print(f"Top {top} tasks by API busy time:")
    for profile in sorted(profiles, key=lambda p: p.api_merged_s, reverse=True)[:top]:
        print(
            f"{profile.trial_name} api_busy={fmt_s(profile.api_merged_s)} "
            f"agent={fmt_s(profile.agent_execution_s)} gap={fmt_s(profile.api_gap_s)} "
            f"calls={profile.api_calls} failures={profile.api_failures}"
        )


def minute_profile(calls: list[ApiCall]) -> list[dict[str, float]]:
    if not calls:
        return []
    first_minute_ms = min(call.start_ms for call in calls) // 60_000 * 60_000
    last_minute_ms = max(call.end_ms for call in calls) // 60_000 * 60_000
    rows = []
    minute = first_minute_ms
    while minute <= last_minute_ms:
        bucket_start = minute
        bucket_end = minute + 60_000
        busy_ms = 0
        requests_started = 0
        for call in calls:
            if bucket_start <= call.start_ms < bucket_end:
                requests_started += 1
            overlap = max(
                0, min(call.end_ms, bucket_end) - max(call.start_ms, bucket_start)
            )
            busy_ms += overlap
        rows.append(
            {
                "minute_start_epoch_ms": minute,
                "requests_started": requests_started,
                "api_busy_s": busy_ms / 1000,
                "avg_api_requests_in_flight": busy_ms / 60_000,
            }
        )
        minute += 60_000
    return rows


def build_markdown_report(
    job_dir: Path,
    profiles: list[TaskProfile],
    calls: list[ApiCall],
    top: int,
) -> str:
    if not profiles:
        return f"# Trajectory Profile\n\nNo completed tasks found for `{job_dir}`.\n"

    total_agent = sum(p.agent_execution_s for p in profiles)
    total_api_merged = sum(p.api_merged_s for p in profiles)
    total_api_sum = sum(call.duration_ms for call in calls) / 1000
    weighted_api_coverage = total_api_merged / total_agent if total_agent else 0
    total_wall = sum(p.total_wall_s for p in profiles)
    job_config = load_job_config(job_dir)
    configured_concurrency = job_config.get("n_concurrent_trials")

    if calls:
        first_ms = min(call.start_ms for call in calls)
        last_ms = max(call.end_ms for call in calls)
        api_window_s = max((last_ms - first_ms) / 1000, 1e-9)
        avg_inflight = total_api_sum / api_window_s
    else:
        api_window_s = 0
        avg_inflight = 0

    minute_rows = minute_profile(calls)
    minute_inflight = [row["avg_api_requests_in_flight"] for row in minute_rows]
    minute_requests = [row["requests_started"] for row in minute_rows]

    phase_rows = []
    for name, values, total_share in [
        ("total_wall", [p.total_wall_s for p in profiles], 1.0),
        (
            "env_setup",
            [p.env_setup_s for p in profiles],
            sum(p.env_setup_s for p in profiles) / total_wall if total_wall else 0,
        ),
        (
            "agent_setup",
            [p.agent_setup_s for p in profiles],
            sum(p.agent_setup_s for p in profiles) / total_wall if total_wall else 0,
        ),
        (
            "agent_execution",
            [p.agent_execution_s for p in profiles],
            sum(p.agent_execution_s for p in profiles) / total_wall
            if total_wall
            else 0,
        ),
        (
            "verifier",
            [p.verifier_s for p in profiles],
            sum(p.verifier_s for p in profiles) / total_wall if total_wall else 0,
        ),
    ]:
        stats = metric_stats(values)
        phase_rows.append(
            [
                name,
                fmt_s(stats["mean"]),
                fmt_pct(total_share),
                fmt_s(stats["p50"]),
                fmt_s(stats["p90"]),
                fmt_s(stats["p95"]),
                fmt_s(stats["max"]),
            ]
        )

    api_rows = []
    for name, values, formatter in [
        ("api_call_sum", [p.api_sum_s for p in profiles], fmt_s),
        ("api_merged_busy", [p.api_merged_s for p in profiles], fmt_s),
        ("non_api_gap", [p.api_gap_s for p in profiles], fmt_s),
        (
            "api_coverage",
            [p.api_coverage for p in profiles if p.agent_execution_s > 0],
            fmt_pct,
        ),
    ]:
        stats = metric_stats(values)
        api_rows.append(
            [
                name,
                formatter(stats["mean"]),
                formatter(stats["p50"]),
                formatter(stats["p90"]),
                formatter(stats["p95"]),
                formatter(stats["max"]),
            ]
        )

    agent_breakdown_rows = []
    for name, values in [
        ("api_merged_busy", [p.api_merged_s for p in profiles]),
        ("pre_first_api_gap", [p.pre_first_api_gap_s for p in profiles]),
        ("between_api_gap", [p.between_api_gap_s for p in profiles]),
        ("post_last_api_gap", [p.post_last_api_gap_s for p in profiles]),
    ]:
        stats = metric_stats(values)
        total_share = sum(values) / total_agent if total_agent else 0
        agent_breakdown_rows.append(
            [
                name,
                fmt_s(stats["mean"]),
                fmt_pct(total_share),
                fmt_s(stats["p50"]),
                fmt_s(stats["p90"]),
                fmt_s(stats["p95"]),
                fmt_s(stats["max"]),
            ]
        )

    top_gap_rows = [
        [
            profile.trial_name,
            fmt_s(profile.api_gap_s),
            fmt_s(profile.agent_execution_s),
            fmt_s(profile.api_merged_s),
            fmt_pct(profile.api_coverage),
            profile.api_calls,
            profile.exception_type or "-",
        ]
        for profile in sorted(profiles, key=lambda p: p.api_gap_s, reverse=True)[:top]
    ]

    top_api_rows = [
        [
            profile.trial_name,
            fmt_s(profile.api_merged_s),
            fmt_s(profile.agent_execution_s),
            fmt_s(profile.api_gap_s),
            profile.api_calls,
            profile.api_failures,
            profile.exception_type or "-",
        ]
        for profile in sorted(profiles, key=lambda p: p.api_merged_s, reverse=True)[
            :top
        ]
    ]

    minute_stats = metric_stats(minute_inflight)
    request_stats = metric_stats([float(v) for v in minute_requests])
    api_failure_rate = (
        sum(p.api_failures for p in profiles) / sum(p.api_calls for p in profiles)
        if sum(p.api_calls for p in profiles)
        else 0
    )
    exception_counts = Counter(p.exception_type or "none" for p in profiles)
    timeout_count = exception_counts.get("AgentTimeoutError", 0)
    non_api_gap_share = 1 - weighted_api_coverage

    conclusion_rows = [
        [
            "API/GPU utilization",
            (
                f"平均在飞 API 请求 {avg_inflight:.2f}"
                + (
                    f"，约为 job 并发 {configured_concurrency} 的 "
                    f"{fmt_pct(avg_inflight / configured_concurrency)}"
                    if isinstance(configured_concurrency, int | float)
                    and configured_concurrency
                    else ""
                )
            ),
            "API 侧不是持续满载；如果后端可承载接近 job 并发，GPU 低利用率部分来自请求供给不足。",
        ],
        [
            "Local/tool gap",
            f"agent_execution 中非 API gap 约 {fmt_pct(non_api_gap_share)}，均值 {fmt_s(statistics.fmean([p.api_gap_s for p in profiles]))}",
            "本地工具调用、Docker/filesystem I/O、agent 思考/调度间隔是主要可优化方向。",
        ],
        [
            "API failures/retries",
            f"API failure rate {fmt_pct(api_failure_rate)} ({fmt_int(sum(p.api_failures for p in profiles))}/{fmt_int(sum(p.api_calls for p in profiles))})",
            "失败会触发 retry/backoff，直接降低有效吞吐；需要优先看 5xx/timeout/限流原因。",
        ],
        [
            "Environment setup",
            f"平均 {fmt_s(statistics.fmean([p.env_setup_s for p in profiles]))}，P95 {fmt_s(quantile([p.env_setup_s for p in profiles], 0.95))}",
            "均值不算主瓶颈，但尾部较重；可考虑镜像/依赖缓存、减少 Docker cleanup/build 等。",
        ],
        [
            "Timeouts",
            f"AgentTimeoutError {timeout_count}/{len(profiles)}",
            "大量任务撞 timeout，说明长尾 task 会长期占住并发槽；可考虑按 task 难度/历史耗时分层运行或限制无效长跑。",
        ],
    ]

    return (
        "\n\n".join(
            [
                "# Trajectory Time Profile",
                f"Job: `{job_dir}`",
                "## Summary\n"
                + markdown_table(
                    ["Metric", "Value"],
                    [
                        ["completed_tasks", len(profiles)],
                        [
                            "tasks_with_api_calls",
                            sum(1 for p in profiles if p.api_calls),
                        ],
                        [
                            "total_api_calls",
                            fmt_int(sum(p.api_calls for p in profiles)),
                        ],
                        [
                            "total_api_failures",
                            fmt_int(sum(p.api_failures for p in profiles)),
                        ],
                        [
                            "total_prompt_tokens",
                            fmt_int(sum(p.prompt_tokens for p in profiles)),
                        ],
                        [
                            "total_completion_tokens",
                            fmt_int(sum(p.completion_tokens for p in profiles)),
                        ],
                        ["weighted_api_coverage", fmt_pct(weighted_api_coverage)],
                        [
                            "configured_concurrency",
                            configured_concurrency if configured_concurrency else "-",
                        ],
                        ["api_window", fmt_s(api_window_s)],
                        ["api_sum_duration", fmt_s(total_api_sum)],
                        ["avg_api_requests_in_flight", f"{avg_inflight:.2f}"],
                    ],
                ),
                "## Phase Wall Time Per Completed Task\n"
                + markdown_table(
                    ["Phase", "Mean", "Total Share", "P50", "P90", "P95", "Max"],
                    phase_rows,
                ),
                "## API vs Local/Tool Time\n"
                + markdown_table(
                    ["Metric", "Mean", "P50", "P90", "P95", "Max"],
                    api_rows,
                ),
                "## Metric Definitions\n"
                + markdown_table(
                    ["Metric", "Meaning"],
                    [
                        [
                            "api_call_sum",
                            "Sum of every LiteLLM request duration. Overlapping requests are counted multiple times, so this can exceed wall time.",
                        ],
                        [
                            "api_merged_busy",
                            "Wall-clock time during `agent_execution` where at least one API request was in flight. Overlapping requests are counted once.",
                        ],
                        [
                            "api_coverage",
                            "`api_merged_busy / agent_execution`. Higher means the agent spends more wall time waiting on the API; lower means more local/tool/Docker gaps.",
                        ],
                        [
                            "non_api_gap",
                            "`agent_execution - api_merged_busy`. This approximates local tool execution, Docker/filesystem I/O, agent scheduling/thinking gaps, and retry/backoff pauses.",
                        ],
                    ],
                ),
                "## Agent Execution Breakdown\n"
                + markdown_table(
                    ["Part", "Mean", "Agent Share", "P50", "P90", "P95", "Max"],
                    agent_breakdown_rows,
                ),
                "## Minute-Level API Concurrency\n"
                + markdown_table(
                    ["Metric", "Mean", "P50", "P90", "P95", "Max"],
                    [
                        [
                            "avg_api_requests_in_flight",
                            f"{minute_stats['mean']:.2f}",
                            f"{minute_stats['p50']:.2f}",
                            f"{minute_stats['p90']:.2f}",
                            f"{minute_stats['p95']:.2f}",
                            f"{minute_stats['max']:.2f}",
                        ],
                        [
                            "requests_started_per_min",
                            f"{request_stats['mean']:.2f}",
                            f"{request_stats['p50']:.2f}",
                            f"{request_stats['p90']:.2f}",
                            f"{request_stats['p95']:.2f}",
                            f"{request_stats['max']:.2f}",
                        ],
                    ],
                ),
                f"## Top {top} Tasks By Non-API Gap\n"
                + markdown_table(
                    [
                        "Trial",
                        "Non-API Gap",
                        "Agent Wall",
                        "API Busy",
                        "API Coverage",
                        "Calls",
                        "Exception",
                    ],
                    top_gap_rows,
                ),
                f"## Top {top} Tasks By API Busy Time\n"
                + markdown_table(
                    [
                        "Trial",
                        "API Busy",
                        "Agent Wall",
                        "Non-API Gap",
                        "Calls",
                        "Failures",
                        "Exception",
                    ],
                    top_api_rows,
                ),
                "## Conclusions And Improvement Ideas\n"
                + markdown_table(
                    ["Area", "Observation", "What To Improve"],
                    conclusion_rows,
                ),
                "## Overall Conclusion\n"
                "当前瓶颈不是单纯的 API/GPU。API 请求平均在飞量低于 job 并发，"
                "同时 agent 执行阶段有明显 non-API gap，且 API failure/retry 比例偏高。"
                "优先建议从三处下手：降低 API failure/retry，减少本地工具与 Docker/filesystem I/O "
                "的等待，处理 timeout 长尾任务对并发槽的占用。",
            ]
        )
        + "\n"
    )


def write_outputs(
    output_dir: Path,
    job_dir: Path,
    profiles: list[TaskProfile],
    calls: list[ApiCall],
    top: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    task_csv = output_dir / "task_profile.csv"
    with task_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(TaskProfile.__dataclass_fields__))
        writer.writeheader()
        for profile in profiles:
            writer.writerow(profile.__dict__)

    minute_csv = output_dir / "api_minute_profile.csv"
    with minute_csv.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "minute_start_epoch_ms",
            "requests_started",
            "api_busy_s",
            "avg_api_requests_in_flight",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(minute_profile(calls))

    report_md = output_dir / "report.md"
    report_md.write_text(
        build_markdown_report(job_dir, profiles, calls, top),
        encoding="utf-8",
    )

    print()
    print(f"Wrote {task_csv}")
    print(f"Wrote {minute_csv}")
    print(f"Wrote {report_md}")


def main() -> None:
    args = parse_args()
    job_dir = args.job_dir.resolve()
    if not job_dir.exists():
        raise SystemExit(f"Job directory does not exist: {job_dir}")

    profiles: list[TaskProfile] = []
    all_calls: list[ApiCall] = []
    completed_trial_dirs = []
    for result_path in sorted(job_dir.glob("*/result.json")):
        trial_dir = result_path.parent
        completed_trial_dirs.append(trial_dir)
        profile = profile_completed_trial(trial_dir)
        if profile:
            profiles.append(profile)
            all_calls.extend(
                read_api_calls(trial_dir / "agent" / "litellm-trajectory.jsonl")
            )

    if args.include_running:
        completed_set = set(completed_trial_dirs)
        for trajectory_path in sorted(job_dir.glob("*/agent/litellm-trajectory.jsonl")):
            trial_dir = trajectory_path.parent.parent
            if trial_dir not in completed_set:
                all_calls.extend(read_api_calls(trajectory_path))

    print(f"job_dir: {job_dir}")
    print_summary(profiles, all_calls, args.top)

    output_dir = args.output_dir or args.csv_dir or DEFAULT_OUTPUT_ROOT / job_dir.name
    write_outputs(output_dir, job_dir, profiles, all_calls, args.top)


if __name__ == "__main__":
    main()
