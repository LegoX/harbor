"""Compare trajectory scores for resolved and unresolved Harbor trials.

This module owns the Harbor/OpenHands SDK integration for the optional
``traj_analysis`` pipeline stage. It is self-contained within this project.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from src.progress import Progress
from src.report_metadata import resolve_instance_id
from src.traj_analysis.scoring import TQS_WEIGHTS, score_dataset


RAW_KEYS = (
    "assistant_turns",
    "total_tool_calls",
)
COMPONENT_KEYS = (
    "oec_score",
    "iac_score",
    "dpi_score",
    "ped_score",
    "psn_score",
    "tte_score",
    "scp_score",
    "sub_score",
    "fec_score",
    "stp_score",
    "tvr_score",
)
COMPOSITE_KEYS = ("composite_score",)
ALL_NUMERIC_KEYS = RAW_KEYS + COMPONENT_KEYS + COMPOSITE_KEYS

METRIC_GLOSSARY: dict[str, tuple[str, str]] = {
    "assistant_turns": (
        "Assistant turns",
        "Number of assistant messages in the trajectory.",
    ),
    "total_tool_calls": (
        "Tool calls",
        "Total tool calls emitted by assistant messages.",
    ),
    "oec_score": (
        "OEC - observation entropy collapse",
        "Whether observation text keeps carrying new information over time.",
    ),
    "iac_score": (
        "IAC - intent/action consistency",
        "How well stated intent matches the next action class.",
    ),
    "dpi_score": (
        "DPI - dirty-pattern penalty",
        "Reverse score for truncation, no-write, loops, repeated errors, etc.",
    ),
    "ped_score": (
        "PED - post-error diversity",
        "Whether the agent changes target files or strategy after errors.",
    ),
    "psn_score": (
        "PSN - progressive scope narrowing",
        "Whether later actions focus on fewer target files than early actions.",
    ),
    "tte_score": (
        "TTE - tool-transition entropy",
        "Diversity of tool/action transitions.",
    ),
    "scp_score": (
        "SCP - first effective edit timing",
        "Whether first successful edit lands in the expected timing window.",
    ),
    "sub_score": (
        "SUB - submission completeness",
        "Finish state adjusted by late errors and late test execution.",
    ),
    "fec_score": (
        "FEC - file-edit concentration",
        "How concentrated edits are across files.",
    ),
    "stp_score": (
        "STP - step efficiency",
        "Efficiency score derived from assistant turn count.",
    ),
    "tvr_score": (
        "TVR - test verification",
        "Whether tests are run after edits and near the end.",
    ),
    "composite_score": (
        "TQS V2 composite",
        "Weighted fail-soft aggregate of available component scores.",
    ),
}

BUCKETS = (
    (">=0.8", 0.8, float("inf")),
    ("0.6-0.8", 0.6, 0.8),
    ("0.4-0.6", 0.4, 0.6),
    ("0.2-0.4", 0.2, 0.4),
    ("<0.2", float("-inf"), 0.2),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--max-instances", type=int, default=None)
    parser.add_argument(
        "--trajectory-subpath",
        default="agent/litellm-trajectory.jsonl",
        help="Trajectory path under each Harbor trial directory.",
    )
    parser.add_argument(
        "--trial-result-file",
        default="result.json",
        help="Trial result filename under each Harbor trial directory.",
    )
    parser.add_argument(
        "--trial-report-subpath",
        default="verifier/report.json",
        help="Verifier report path under each Harbor trial directory.",
    )
    return parser.parse_args()


def _component_weights() -> dict[str, float]:
    return {f"{key}_score": weight for key, weight in TQS_WEIGHTS.items()}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return first.get("text", "")
    return ""


def _normalize_tool_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = arguments
    elif arguments is None:
        parsed = {}
    else:
        parsed = arguments
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            function = {}
        out: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": function.get("name", ""),
                "arguments": _normalize_tool_arguments(function.get("arguments")),
            },
        }
        call_id = tool_call.get("id")
        if isinstance(call_id, str) and call_id:
            out["id"] = call_id
        normalized.append(out)
    return normalized


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if role in ("system", "user"):
        return {"role": role, "content": _extract_text(message.get("content"))}
    if role == "tool":
        out = {"role": "tool", "content": _extract_text(message.get("content"))}
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            out["tool_call_id"] = tool_call_id
        return out
    if role == "assistant":
        out = {
            "role": "assistant",
            "content": _extract_text(message.get("content")),
            "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
        }
        if message.get("reasoning_content") is not None:
            out["reasoning_content"] = message["reasoning_content"]
        return out
    return {"role": role, "content": _extract_text(message.get("content"))}


def _build_messages_from_logger_record(
    record: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, str | None]:
    raw_messages = record["request_body"]["messages"]
    messages = [_normalize_message(message) for message in raw_messages]

    response_body = record.get("response_body") or {}
    choices = response_body.get("choices") or []
    if not choices:
        return None, "no_choices"

    messages.append(_normalize_message(choices[0]["message"]))
    return messages, None


def _read_last_jsonl_record(jsonl_path: Path) -> dict[str, Any] | None:
    chunk_size = 8192
    with jsonl_path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buffer = b""

        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buffer = f.read(read_size) + buffer

            lines = buffer.splitlines()
            complete_lines = lines if pos == 0 else lines[1:]
            for raw_line in reversed(complete_lines):
                line = raw_line.strip()
                if line:
                    return json.loads(line.decode("utf-8"))

        return None


def _infer_think_mode(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "assistant" and message.get("reasoning_content"):
            return "slow"
    return "fast"


def _check_roles(messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return False

    start_idx = 1 if messages[0].get("role") == "system" else 0
    if start_idx >= len(messages):
        return False
    if messages[start_idx].get("role") != "user":
        return False
    if messages[-1].get("role") != "assistant":
        return False

    for idx in range(start_idx + 1, len(messages)):
        role = messages[idx].get("role")
        prev_role = messages[idx - 1].get("role")
        if prev_role == "assistant":
            if role not in ("tool", "user"):
                return False
        elif prev_role == "tool":
            if role not in ("tool", "assistant"):
                return False
        elif prev_role == "user":
            if role != "assistant":
                return False
        else:
            return False
    return True


def _check_reasoning_content(
    messages: list[dict[str, Any]],
    think_mode: str,
    pseudo_turns: int | None,
) -> bool:
    if think_mode == "fast":
        return True

    check_turns = pseudo_turns if pseudo_turns else 0
    for idx in range(check_turns, len(messages)):
        message = messages[idx]
        if message.get("role") == "assistant" and not message.get("reasoning_content"):
            return False
    return True


def _save_jsonl(output_path: Path, records: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _resolved_from_verifier_report(report_path: Path, instance_id: str) -> bool | None:
    if not report_path.exists():
        return None
    try:
        report = _read_json(report_path)
        entry = report.get(instance_id)
        if isinstance(entry, dict) and "resolved" in entry:
            return bool(entry["resolved"])
    except Exception:
        return None
    return None


def _split_from_aggregate_result(result_path: Path) -> dict[str, list[str]] | None:
    if not result_path.exists():
        return None
    result = _read_json(result_path)
    evals = (result.get("stats") or {}).get("evals")
    if not isinstance(evals, dict):
        return None

    out = {"resolved": [], "unresolved": []}
    for eval_result in evals.values():
        reward_stats = (eval_result.get("reward_stats") or {}).get("reward") or {}
        out["resolved"].extend(reward_stats.get("1.0", []))
        out["unresolved"].extend(reward_stats.get("0.0", []))
    return out


def split_folders_by_reward(
    job_dir: Path,
    *,
    trial_result_file: str = "result.json",
    trial_report_subpath: str = "verifier/report.json",
) -> dict[str, list[str]]:
    """Split Harbor trial folder names by verifier reward/resolved status."""
    aggregate_split = _split_from_aggregate_result(job_dir / trial_result_file)
    if aggregate_split is not None:
        return aggregate_split

    out = {"resolved": [], "unresolved": []}
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        result_path = trial_dir / trial_result_file
        result = _read_json(result_path) if result_path.exists() else {}
        instance_id = resolve_instance_id(trial_dir, result=result)
        reward = (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
        verifier_resolved = _resolved_from_verifier_report(
            trial_dir / trial_report_subpath,
            instance_id,
        )
        if verifier_resolved is True or reward == 1.0:
            out["resolved"].append(trial_dir.name)
        elif verifier_resolved is False or reward == 0.0:
            out["unresolved"].append(trial_dir.name)
    return out


def build_im_for_folders(
    job_dir: Path,
    folders: list[str],
    max_samples: int | None,
    label: str,
    *,
    trajectory_subpath: str = "agent/litellm-trajectory.jsonl",
    trial_result_file: str = "result.json",
) -> list[dict[str, Any]]:
    im_records: list[dict[str, Any]] = []
    skipped_no_traj = skipped_no_choices = skipped_invalid = 0

    progress = Progress(f"traj_analysis {label} folders", len(folders))
    try:
        for folder_name in folders:
            progress.update()
            trial_dir = job_dir / folder_name
            result_path = trial_dir / trial_result_file
            result = _read_json(result_path) if result_path.exists() else {}
            instance_id = resolve_instance_id(trial_dir, result=result)
            trajectory_path = trial_dir / trajectory_subpath

            try:
                last_record = _read_last_jsonl_record(trajectory_path)
            except FileNotFoundError:
                skipped_no_traj += 1
                continue
            except (json.JSONDecodeError, OSError) as exc:
                skipped_invalid += 1
                print(f"[{label}] failed to read {instance_id}: {exc}")
                continue

            if last_record is None:
                skipped_no_traj += 1
                continue

            try:
                messages, err = _build_messages_from_logger_record(last_record)
            except (KeyError, TypeError, IndexError) as exc:
                skipped_invalid += 1
                print(f"[{label}] failed to parse {instance_id}: {exc}")
                continue
            if err == "no_choices" or messages is None:
                skipped_no_choices += 1
                continue

            tools = (last_record.get("request_body") or {}).get("tools", [])
            think_mode = _infer_think_mode(messages)
            if not _check_roles(messages):
                skipped_invalid += 1
                continue
            if not _check_reasoning_content(
                messages,
                think_mode=think_mode,
                pseudo_turns=None,
            ):
                skipped_invalid += 1
                continue

            im_records.append(
                {
                    "messages": messages,
                    "tools": tools,
                    "pseudo_turns": None,
                    "think_mode": think_mode,
                    "_instance_id": instance_id,
                }
            )
            if max_samples is not None and len(im_records) >= max_samples:
                break
    finally:
        progress.close()

    print(
        f"[{label}] sampled={len(im_records)} skipped: no_traj={skipped_no_traj} "
        f"no_choices={skipped_no_choices} invalid={skipped_invalid}"
    )
    return im_records


def _tool_name_from_openhands_action(event: dict[str, Any]) -> str:
    """Map OpenHands ActionEvent metadata to the tool names used by TQS."""
    tool_name = event.get("tool_name")
    if isinstance(tool_name, str) and tool_name:
        return tool_name

    action = event.get("action") or {}
    kind = action.get("kind")
    return {
        "TerminalAction": "terminal",
        "FileEditorAction": "file_editor",
        "TaskTrackerAction": "task_tracker",
        "FinishAction": "finish",
        "ThinkAction": "think",
    }.get(kind, str(kind or ""))


def _extract_openhands_text(value: Any) -> str:
    """Normalize OpenHands content blocks to plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
                elif item.get("content") is not None:
                    parts.append(_extract_openhands_text(item.get("content")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        if value.get("text") is not None:
            return str(value["text"])
        if value.get("content") is not None:
            return _extract_openhands_text(value["content"])
    return str(value)


def _normalize_openhands_tools(tools: Any) -> list[dict[str, Any]]:
    """Convert OpenHands tool descriptors into OpenAI-style function tools."""
    if not isinstance(tools, list):
        return []

    normalized = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("title")
        if not name:
            action_type = tool.get("action_type")
            name = {
                "TerminalAction": "terminal",
                "FileEditorAction": "file_editor",
                "TaskTrackerAction": "task_tracker",
                "FinishAction": "finish",
                "ThinkAction": "think",
            }.get(action_type, action_type)
        if not name:
            continue
        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": str(name),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") or {},
                },
            }
        )
    return normalized


def _openhands_tool_call(event: dict[str, Any]) -> dict[str, Any]:
    action = event.get("action") or {}
    arguments = {
        key: value
        for key, value in action.items()
        if key != "kind" and value is not None
    }
    tool_name = _tool_name_from_openhands_action(event)
    out: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": _normalize_tool_arguments(arguments),
        },
    }
    call_id = event.get("tool_call_id") or event.get("id")
    if isinstance(call_id, str) and call_id:
        out["id"] = call_id
    return out


def _build_messages_from_openhands_entry(
    entry: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build IM messages/tools from one OpenHands JSONL trajectory entry."""
    messages: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    for event in entry.get("history") or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")

        if kind == "SystemPromptEvent":
            system_prompt = event.get("system_prompt")
            if system_prompt:
                messages.append({"role": "system", "content": str(system_prompt)})
            if not tools:
                tools = _normalize_openhands_tools(event.get("tools"))
            continue

        if kind == "MessageEvent":
            llm_message = event.get("llm_message") or {}
            role = llm_message.get("role")
            if role in ("system", "user", "assistant"):
                msg = {
                    "role": role,
                    "content": _extract_openhands_text(llm_message.get("content")),
                }
                if role == "assistant":
                    msg["tool_calls"] = _normalize_tool_calls(
                        llm_message.get("tool_calls")
                    )
                    if llm_message.get("reasoning_content") is not None:
                        msg["reasoning_content"] = llm_message["reasoning_content"]
                messages.append(msg)
            continue

        if kind == "ActionEvent":
            action = event.get("action") or {}
            thought = _extract_openhands_text(
                event.get("thought")
            ) or _extract_openhands_text(action.get("thought"))
            msg = {
                "role": "assistant",
                "content": thought,
                "tool_calls": [_openhands_tool_call(event)],
            }
            reasoning = event.get("reasoning_content")
            if reasoning is not None:
                msg["reasoning_content"] = str(reasoning)
            messages.append(msg)
            continue

        if kind == "ObservationEvent":
            observation = event.get("observation") or {}
            msg = {
                "role": "tool",
                "content": _extract_openhands_text(observation),
            }
            call_id = event.get("tool_call_id") or event.get("action_id")
            if isinstance(call_id, str) and call_id:
                msg["tool_call_id"] = call_id
            messages.append(msg)

    return messages, tools


def split_instances_by_report(report_path: Path) -> dict[str, list[str]]:
    """Split instance IDs using an OpenHands aggregate report JSON."""
    report = _read_json(report_path)
    resolved = set(report.get("resolved_ids") or [])

    completed = set(report.get("completed_ids") or [])
    unresolved = set(report.get("unresolved_ids") or [])
    if not unresolved and completed:
        unresolved = completed - resolved

    unresolved.update(report.get("empty_patch_ids") or [])
    unresolved.update(report.get("error_ids") or [])
    unresolved.difference_update(resolved)

    return {
        "resolved": sorted(resolved),
        "unresolved": sorted(unresolved),
    }


def build_im_for_openhands_jsonl(
    trajectory_path: Path,
    instance_ids: list[str],
    max_samples: int | None,
    label: str,
) -> list[dict[str, Any]]:
    """Build scored IM input records from OpenHands aggregate JSONL output."""
    wanted = set(instance_ids)
    im_records: list[dict[str, Any]] = []
    skipped_missing_history = skipped_invalid = 0

    progress = Progress(f"traj_analysis {label} jsonl records")
    try:
        with trajectory_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                progress.update()
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    skipped_invalid += 1
                    print(
                        f"[{label}] malformed JSON at {trajectory_path}:{line_no}: {exc}"
                    )
                    continue

                instance_id = str(entry.get("instance_id") or "")
                if instance_id not in wanted:
                    continue
                if not entry.get("history"):
                    skipped_missing_history += 1
                    continue

                try:
                    messages, tools = _build_messages_from_openhands_entry(entry)
                except (TypeError, ValueError) as exc:
                    skipped_invalid += 1
                    print(f"[{label}] failed to parse {instance_id}: {exc}")
                    continue
                if not messages:
                    skipped_missing_history += 1
                    continue

                think_mode = _infer_think_mode(messages)
                im_records.append(
                    {
                        "messages": messages,
                        "tools": tools,
                        "pseudo_turns": None,
                        "think_mode": think_mode,
                        "_instance_id": instance_id,
                    }
                )
                if max_samples is not None and len(im_records) >= max_samples:
                    break
    finally:
        progress.close()

    print(
        f"[{label}] sampled={len(im_records)} skipped: "
        f"missing_history={skipped_missing_history} invalid={skipped_invalid}"
    )
    return im_records


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = dx2 = dy2 = 0.0
    for x, y in zip(xs, ys):
        dx, dy = x - mx, y - my
        num += dx * dy
        dx2 += dx * dx
        dy2 += dy * dy
    denom = math.sqrt(dx2 * dy2)
    if denom == 0:
        return None
    return num / denom


def _rank(xs: list[float]) -> list[float]:
    indexed = sorted(enumerate(xs), key=lambda item: item[1])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - lbeta) / a
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-10:
            break
    return front * h


def _approx_p_value(r: float | None, n: int) -> float | None:
    if r is None or n < 3:
        return None
    if r >= 1.0 or r <= -1.0:
        return 0.0
    df = n - 2
    t = r * math.sqrt(df / (1.0 - r * r))
    x = df / (df + t * t)
    return _betainc(df / 2.0, 0.5, x)


def compute_correlations(
    resolved_records: list[dict[str, Any]],
    unresolved_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    paired: dict[str, list[tuple[float, int]]] = {}

    def ingest(records: list[dict[str, Any]], y: int) -> None:
        for record in records:
            score = record.get("_score")
            if not isinstance(score, dict):
                continue
            for key in ALL_NUMERIC_KEYS:
                value = score.get(key)
                if not isinstance(value, (int, float)):
                    continue
                if isinstance(value, float) and math.isnan(value):
                    continue
                paired.setdefault(key, []).append((float(value), y))

    ingest(resolved_records, 1)
    ingest(unresolved_records, 0)

    out: dict[str, dict[str, Any]] = {}
    for key in ALL_NUMERIC_KEYS:
        pairs = paired.get(key)
        if not pairs or len(pairs) < 3:
            continue
        xs = [value for value, _ in pairs]
        ys = [float(y) for _, y in pairs]
        n = len(pairs)
        n_resolved = sum(1 for _, y in pairs if y)
        n_unresolved = n - n_resolved
        pearson_r = _pearson(xs, ys)
        spearman_r = _spearman(xs, ys)
        out[key] = {
            "n": n,
            "n_resolved": n_resolved,
            "n_unresolved": n_unresolved,
            "mean_resolved": (
                sum(value for value, y in pairs if y) / n_resolved
                if n_resolved
                else None
            ),
            "mean_unresolved": (
                sum(value for value, y in pairs if not y) / n_unresolved
                if n_unresolved
                else None
            ),
            "pearson_r": pearson_r,
            "pearson_p": _approx_p_value(pearson_r, n),
            "spearman_r": spearman_r,
            "spearman_p": _approx_p_value(spearman_r, n),
        }
    return out


def _row_label(key: str, weights: dict[str, float]) -> str:
    if key in weights:
        return f"{key} (w={weights[key]:.2f})"
    return key


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [
        record["_score"] for record in records if isinstance(record.get("_score"), dict)
    ]
    composite = [
        score["composite_score"] for score in scores if "composite_score" in score
    ]
    if not composite:
        return {"n": 0}

    summary: dict[str, Any] = {
        "n": len(composite),
        "buckets": {
            name: sum(1 for value in composite if lo <= value < hi)
            for name, lo, hi in BUCKETS
        },
        "metrics": {},
    }
    for key in ALL_NUMERIC_KEYS:
        values = [
            score[key] for score in scores if isinstance(score.get(key), (int, float))
        ]
        if values:
            summary["metrics"][key] = _stats(values)
    return summary


def format_correlations(correlations: dict[str, dict[str, Any]]) -> str:
    if not correlations:
        return "(no numeric features available for correlation)"

    sections = [
        ("Raw counts", RAW_KEYS),
        ("Components", COMPONENT_KEYS),
        ("Composite", COMPOSITE_KEYS),
    ]
    weights = _component_weights()

    def fmt(value: Any, width: int = 9, digits: int = 4) -> str:
        return (
            f"{value:>{width}.{digits}f}"
            if isinstance(value, (int, float))
            else f"{'NA':>{width}}"
        )

    lines: list[str] = ["-- Correlation with resolved (0/1) --"]
    lines.append(
        "Pearson r against the 0/1 indicator is the point-biserial correlation; "
        "p-values are two-sided t-approximations."
    )
    header = (
        f"{'metric':<32}{'n':>6}"
        f"{'pearson_r':>12}{'p(pearson)':>12}"
        f"{'spearman_r':>12}{'p(spearman)':>13}"
    )
    for title, keys in sections:
        present = [key for key in keys if key in correlations]
        if not present:
            continue
        lines.append("")
        lines.append(f"-- {title} --")
        lines.append(header)
        for key in present:
            value = correlations[key]
            lines.append(
                f"{_row_label(key, weights):<32}{value['n']:>6}"
                f"{fmt(value['pearson_r'])}{fmt(value['pearson_p'])}"
                f"{fmt(value['spearman_r'])}{fmt(value['spearman_p'], 13)}"
            )
    return "\n".join(lines)


def format_comparison(
    resolved_summary: dict[str, Any],
    unresolved_summary: dict[str, Any],
    correlations: dict[str, dict[str, Any]] | None = None,
) -> str:
    resolved_metrics = resolved_summary.get("metrics", {})
    unresolved_metrics = unresolved_summary.get("metrics", {})

    def fmt(value: float | None) -> str:
        return "-" if value is None else f"{value:.3f}"

    sections = [
        ("Raw counts", RAW_KEYS),
        ("Components", COMPONENT_KEYS),
        ("Composite", COMPOSITE_KEYS),
    ]
    weights = _component_weights()

    n_resolved = resolved_summary.get("n", 0)
    n_unresolved = unresolved_summary.get("n", 0)
    lines: list[str] = [
        f"=== Comparison: resolved (n={n_resolved}) vs unresolved (n={n_unresolved}) ==="
    ]

    for title, keys in sections:
        lines.append("")
        lines.append(f"-- {title} --")
        lines.append(
            f"{'metric':<32}"
            f"{'R.mean':>10}{'U.mean':>10}{'delta':>10}"
            f"{'R.med':>10}{'U.med':>10}"
            f"{'R.min':>10}{'R.max':>10}"
            f"{'U.min':>10}{'U.max':>10}"
        )
        for key in keys:
            resolved = resolved_metrics.get(key)
            unresolved = unresolved_metrics.get(key)
            if resolved is None and unresolved is None:
                continue
            resolved = resolved or {}
            unresolved = unresolved or {}
            r_mean = resolved.get("mean")
            u_mean = unresolved.get("mean")
            delta = (
                r_mean - u_mean if (r_mean is not None and u_mean is not None) else None
            )
            lines.append(
                f"{_row_label(key, weights):<32}"
                f"{fmt(r_mean):>10}{fmt(u_mean):>10}{fmt(delta):>10}"
                f"{fmt(resolved.get('median')):>10}{fmt(unresolved.get('median')):>10}"
                f"{fmt(resolved.get('min')):>10}{fmt(resolved.get('max')):>10}"
                f"{fmt(unresolved.get('min')):>10}{fmt(unresolved.get('max')):>10}"
            )

    lines.append("")
    lines.append("-- Composite bucket distribution --")
    lines.append(f"{'bucket':<12}{'resolved':>14}{'unresolved':>14}")
    resolved_buckets = resolved_summary.get("buckets", {})
    unresolved_buckets = unresolved_summary.get("buckets", {})
    for name, _, _ in BUCKETS:
        lines.append(
            f"{name:<12}{resolved_buckets.get(name, 0):>14}"
            f"{unresolved_buckets.get(name, 0):>14}"
        )

    if correlations:
        lines.append("")
        lines.append(format_correlations(correlations))

    return "\n".join(lines)


def format_glossary() -> str:
    sections = [
        ("Raw counts", RAW_KEYS),
        ("Components", COMPONENT_KEYS),
        ("Composite", COMPOSITE_KEYS),
    ]
    weights = _component_weights()
    lines: list[str] = ["-- Metric glossary --"]
    for title, keys in sections:
        lines.append("")
        lines.append(f"[{title}]")
        for key in keys:
            label, description = METRIC_GLOSSARY.get(key, (key, ""))
            lines.append(f"  {_row_label(key, weights):<20} {label}")
            lines.append(f"  {'':<20} {description}")
    return "\n".join(lines)


def format_composite_formula() -> str:
    weights = _component_weights()
    lines: list[str] = ["-- Composite score formula (TQS V2) --"]
    lines.append("")
    lines.append(
        "composite_score = sum(weight_i * adjusted_component_i) / sum(weight_i)"
    )
    lines.append(
        "Only components present for the current trajectory contribute to the denominator."
    )
    lines.append("")
    lines.append("Nonlinear adjustment before weighting:")
    lines.append("  oec / iac / scp / fec: value = value ** 5")
    lines.append("  dpi: value = value ** 3")
    lines.append("  other components: unchanged")
    lines.append("")
    lines.append("Current weights from src.traj_analysis.scoring.TQS_WEIGHTS:")
    if weights:
        active = [(key, weight) for key, weight in weights.items() if weight > 0]
        zero = [key for key, weight in weights.items() if weight == 0]
        active.sort(key=lambda item: -item[1])
        for key, weight in active:
            lines.append(f"  {key:<12} {weight:.2f}")
        if zero:
            lines.append("  weight=0 diagnostic-only components: " + ", ".join(zero))
        lines.append(
            f"  sum active weights = {sum(weight for _, weight in active):.2f}"
        )
    else:
        lines.append("  traj_analysis module unavailable; weights unknown")
    return "\n".join(lines)


def print_comparison(
    resolved_summary: dict[str, Any],
    unresolved_summary: dict[str, Any],
    correlations: dict[str, dict[str, Any]] | None = None,
) -> None:
    print()
    print(format_comparison(resolved_summary, unresolved_summary, correlations))


def run_comparison(
    job_dir: Path,
    out_dir: Path,
    max_instances: int | None = None,
    *,
    trajectory_subpath: str = "agent/litellm-trajectory.jsonl",
    trial_result_file: str = "result.json",
    trial_report_subpath: str = "verifier/report.json",
) -> dict[str, dict[str, Any]]:
    """Score resolved/unresolved Harbor trajectories and write report artifacts."""
    job_dir = Path(job_dir).resolve()
    if not job_dir.exists():
        raise FileNotFoundError(job_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    splits = split_folders_by_reward(
        job_dir,
        trial_result_file=trial_result_file,
        trial_report_subpath=trial_report_subpath,
    )
    print(
        f"job: {job_dir.name}\n"
        f"  resolved folders : {len(splits['resolved'])}\n"
        f"  unresolved folders: {len(splits['unresolved'])}"
    )

    summaries: dict[str, dict[str, Any]] = {}
    scored: dict[str, list[dict[str, Any]]] = {}
    for label in ("resolved", "unresolved"):
        im_records = build_im_for_folders(
            job_dir,
            splits[label],
            max_instances,
            label,
            trajectory_subpath=trajectory_subpath,
            trial_result_file=trial_result_file,
        )
        print(f"[{label}] scoring {len(im_records)} records")
        im_records = score_dataset(im_records, quiet=True, show_progress=True)
        out_path = out_dir / f"{label}_im.jsonl"
        _save_jsonl(out_path, im_records)
        print(f"[{label}] wrote {len(im_records)} records -> {out_path}")
        summaries[label] = summarize(im_records)
        scored[label] = im_records

    correlations = compute_correlations(scored["resolved"], scored["unresolved"])
    summaries["correlations"] = correlations

    print_comparison(summaries["resolved"], summaries["unresolved"], correlations)

    summary_path = out_dir / "score_comparison.json"
    summary_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False))
    print(f"\nsummary -> {summary_path}")

    report_path = out_dir / "report_comparison.txt"
    header = (
        "======================================================================\n"
        "Trajectory Score Comparison: resolved vs unresolved\n"
        "======================================================================\n"
        f"job_dir: {job_dir}\n"
    )
    report_path.write_text(
        header
        + format_comparison(
            summaries["resolved"], summaries["unresolved"], correlations
        )
        + "\n\n"
        + format_glossary()
        + "\n\n"
        + format_composite_formula()
        + "\n",
        encoding="utf-8",
    )
    print(f"report  -> {report_path}")
    return summaries


def run_openhands_jsonl_comparison(
    trajectory_path: Path,
    report_path: Path,
    out_dir: Path,
    max_instances: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Score resolved/unresolved OpenHands aggregate JSONL trajectories."""
    trajectory_path = Path(trajectory_path).resolve()
    report_path = Path(report_path).resolve()
    if not trajectory_path.exists():
        raise FileNotFoundError(trajectory_path)
    if not report_path.exists():
        raise FileNotFoundError(report_path)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    splits = split_instances_by_report(report_path)
    print(
        f"trajectory: {trajectory_path}\n"
        f"  resolved instances : {len(splits['resolved'])}\n"
        f"  unresolved instances: {len(splits['unresolved'])}"
    )

    summaries: dict[str, dict[str, Any]] = {}
    scored: dict[str, list[dict[str, Any]]] = {}
    for label in ("resolved", "unresolved"):
        im_records = build_im_for_openhands_jsonl(
            trajectory_path,
            splits[label],
            max_instances,
            label,
        )
        print(f"[{label}] scoring {len(im_records)} records")
        im_records = score_dataset(im_records, quiet=True, show_progress=True)
        out_path = out_dir / f"{label}_im.jsonl"
        _save_jsonl(out_path, im_records)
        print(f"[{label}] wrote {len(im_records)} records -> {out_path}")
        summaries[label] = summarize(im_records)
        scored[label] = im_records

    correlations = compute_correlations(scored["resolved"], scored["unresolved"])
    summaries["correlations"] = correlations

    print_comparison(summaries["resolved"], summaries["unresolved"], correlations)

    summary_path = out_dir / "score_comparison.json"
    summary_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False))
    print(f"\nsummary -> {summary_path}")

    report_out_path = out_dir / "report_comparison.txt"
    header = (
        "======================================================================\n"
        "Trajectory Score Comparison: resolved vs unresolved\n"
        "======================================================================\n"
        f"trajectory_path: {trajectory_path}\n"
        f"report_path: {report_path}\n"
    )
    report_out_path.write_text(
        header
        + format_comparison(
            summaries["resolved"], summaries["unresolved"], correlations
        )
        + "\n\n"
        + format_glossary()
        + "\n\n"
        + format_composite_formula()
        + "\n",
        encoding="utf-8",
    )
    print(f"report  -> {report_out_path}")
    return summaries


def main() -> None:
    args = parse_args()
    run_comparison(
        args.job_dir,
        args.out_dir,
        args.max_instances,
        trajectory_subpath=args.trajectory_subpath,
        trial_result_file=args.trial_result_file,
        trial_report_subpath=args.trial_report_subpath,
    )


if __name__ == "__main__":
    main()
