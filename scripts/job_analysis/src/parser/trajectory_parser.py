"""Parse agent trajectories into normalized steps."""

from __future__ import annotations

import json
import logging
import os
import re
import difflib
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.progress import Progress
from src.report_metadata import resolve_instance_id

logger = logging.getLogger(__name__)

ERROR_RE = re.compile(r"\b(error|exception|traceback|failed)\b", re.IGNORECASE)
FUNC_CLASS_RE = re.compile(r"(?:def|class)\s+([A-Za-z_]\w*)")


@dataclass
class TrajectoryStep:
    """Normalized representation of a single trajectory step."""

    step_id: int
    timestamp: str
    action_type: str  # file_view, file_edit, terminal_cmd, finish, other
    action_detail: str  # file path, command, or finish message
    observation: str  # observation content (truncated)
    thought: str  # agent's reasoning
    reasoning_content: str  # chain-of-thought
    is_error: bool
    raw_kind: str  # original event kind


@dataclass
class Trajectory:
    """Parsed trajectory for a single instance."""

    instance_id: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    max_iterations: int = 0
    model_patch: str = ""
    instruction: str = ""
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


def _classify_action(step: dict) -> tuple[str, str]:
    """Classify an ActionEvent into action_type and action_detail."""
    action = step.get("action", {}) or {}
    kind = action.get("kind", "")
    tool_name = step.get("tool_name", "")

    if kind == "FinishAction" or tool_name == "finish":
        msg = action.get("message", "")
        return "finish", msg[:500]

    if kind == "FileEditorAction" or tool_name == "file_editor":
        command = action.get("command", "")
        path = action.get("path", "")
        if command == "view":
            return "file_view", path
        elif command in ("create", "str_replace", "insert"):
            return "file_edit", path
        return "file_edit", path

    if kind == "TerminalAction" or tool_name == "terminal":
        cmd = action.get("command", "")
        return "terminal_cmd", cmd[:500]

    return "other", ""


def _text_from_content(content) -> str:
    """Normalize OpenAI/OpenHands text content shapes to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("text") is not None:
                    parts.append(str(item["text"]))
                elif item.get("content") is not None:
                    parts.append(_text_from_content(item["content"]))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _extract_thought(step: dict) -> str:
    """Extract thought/reasoning from an ActionEvent."""
    thought_parts = []
    thought = step.get("thought", [])
    if isinstance(thought, list):
        for t in thought:
            if isinstance(t, dict) and t.get("text"):
                thought_parts.append(t["text"])
            elif isinstance(t, str):
                thought_parts.append(t)
    elif isinstance(thought, str):
        thought_parts.append(thought)

    return " ".join(thought_parts)[:2000]


def _extract_observation(step: dict) -> str:
    """Extract observation content from an ObservationEvent."""
    obs = step.get("observation", {})
    if isinstance(obs, dict):
        content = obs.get("content", [])
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("text"):
                    parts.append(c["text"])
                elif isinstance(c, str):
                    parts.append(c)
            text = " ".join(parts)
        elif isinstance(content, str):
            text = content
        else:
            text = str(obs)
    elif isinstance(obs, str):
        text = obs
    else:
        text = ""

    # Truncate to keep memory reasonable
    return text[:3000]


def _is_error_observation(step: dict) -> bool:
    """Check if an observation indicates an error."""
    obs = step.get("observation", {})
    if isinstance(obs, dict):
        # Check for error indicators
        if obs.get("error") or obs.get("is_error"):
            return True
        content = obs.get("content", [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict):
                    text = c.get("text", "")
                    if "Error" in text[:200] or "error" in text[:200]:
                        return True
    return False


def _is_error_text(text: str) -> bool:
    """Check if plain text likely describes a tool error."""
    return bool(ERROR_RE.search(text[:500]))


def _parse_tool_arguments(raw) -> dict:
    """Parse OpenAI tool-call arguments."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_call_id(tool_call: dict) -> str:
    return tool_call.get("id") or tool_call.get("tool_call_id") or ""


def _classify_litellm_tool_call(tool_name: str, arguments: dict) -> tuple[str, str]:
    """Classify LiteLLM/OpenAI tool calls into normalized action types."""
    if tool_name == "finish":
        return "finish", str(arguments.get("message", ""))[:500]

    if tool_name in ("terminal", "bash", "shell"):
        return "terminal_cmd", str(arguments.get("command", ""))[:500]

    if tool_name == "file_editor":
        command = arguments.get("command", "")
        path = str(arguments.get("path", ""))
        if command == "view":
            return "file_view", path
        if command in ("create", "str_replace", "insert", "undo_edit"):
            return "file_edit", path
        return "file_edit", path

    return "other", tool_name


def _normalize_patch_path(path: str) -> str:
    """Convert runtime absolute paths into repo-relative patch paths."""
    for prefix in ("/testbed/", "/workspace/", "/app/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path.lstrip("/")


def _split_patch_lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines()


def _hunk_context(*texts: str) -> str:
    """Find a useful function/class context for a synthetic hunk header."""
    for text in texts:
        match = FUNC_CLASS_RE.search(text or "")
        if match:
            return match.group(0)
    return ""


def _format_hunk_line(prefix: str, line: str) -> str:
    return f"{prefix}{line}"


def _synthetic_edit_diff(
    path: str, old_text: str, new_text: str, *, is_create: bool = False
) -> str:
    """Build a small unified diff from one file-editor edit."""
    rel_path = _normalize_patch_path(path)
    old_lines = [] if is_create else _split_patch_lines(old_text)
    new_lines = _split_patch_lines(new_text)
    fromfile = "/dev/null" if is_create else f"a/{rel_path}"
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=fromfile,
            tofile=f"b/{rel_path}",
            lineterm="",
            n=3,
        )
    )
    if not diff_lines:
        return ""

    context = _hunk_context(old_text, new_text)
    if context:
        diff_lines = [
            f"{line} {context}" if line.startswith("@@") else line
            for line in diff_lines
        ]

    lines = [f"diff --git a/{rel_path} b/{rel_path}"]
    if is_create:
        lines.append("new file mode 100644")
    lines.extend(diff_lines)
    return "\n".join(lines)


def _reconstruct_patch_from_edits(
    edits: list[dict], exclude_paths: set[str] | None = None
) -> str:
    """Reconstruct a synthetic predict patch from file_editor edit calls."""
    exclude_paths = exclude_paths or set()
    hunks = []
    for edit in edits:
        command = edit.get("command")
        path = str(edit.get("path", ""))
        rel_path = _normalize_patch_path(path) if path else ""
        if not path or rel_path in exclude_paths:
            continue

        if command == "str_replace":
            old_text = str(edit.get("old_str", ""))
            new_text = str(edit.get("new_str", ""))
            if old_text == new_text:
                continue
            hunks.append(_synthetic_edit_diff(path, old_text, new_text))
        elif command == "create":
            file_text = str(edit.get("file_text", ""))
            hunks.append(_synthetic_edit_diff(path, "", file_text, is_create=True))
        elif command == "insert":
            new_text = str(edit.get("new_str", ""))
            if new_text:
                hunks.append(_synthetic_edit_diff(path, "", new_text))

    return "\n\n".join(h for h in hunks if h).strip()


def _parse_numbered_file_lines(text: str) -> list[str]:
    """Parse file_editor cat -n output into plain file lines."""
    if not text:
        return []

    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    numbered_re = re.compile(r"^\s*\d+(?:\t(.*))?$")
    lines = []
    for raw_line in text.splitlines():
        line = ansi_re.sub("", raw_line)
        match = numbered_re.match(line)
        if match:
            lines.append(match.group(1) or "")
    return lines


def _is_successful_file_edit_observation(text: str) -> bool:
    """Return whether a file_editor mutating observation appears successful."""
    if not text:
        return True
    lower = text[:1000].lower().lstrip()
    if "has been edited" in lower or "has been created" in lower:
        return True
    return not lower.startswith(("error", "failed", "exception"))


def _find_enclosing_context(lines: list[str], start_line: int) -> str:
    """Find the nearest def/class around a 1-indexed hunk start line."""
    if not lines:
        return ""
    index = min(max(start_line - 1, 0), len(lines) - 1)
    for i in range(index, min(len(lines), index + 10)):
        match = FUNC_CLASS_RE.search(lines[i])
        if match:
            return match.group(0)
    for i in range(index, -1, -1):
        match = FUNC_CLASS_RE.search(lines[i])
        if match:
            return match.group(0)
    return ""


def _add_hunk_context(
    diff_lines: list[str], old_lines: list[str], new_lines: list[str]
) -> list[str]:
    """Add best-effort def/class context to unified diff hunk headers."""
    enriched = []
    for line in diff_lines:
        if line.startswith("@@") and line.endswith("@@"):
            match = re.match(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@$", line)
            if match:
                old_start = int(match.group(1))
                new_start = int(match.group(2))
                context = _find_enclosing_context(
                    old_lines, old_start
                ) or _find_enclosing_context(new_lines, new_start)
                if context:
                    line = f"{line} {context}"
        enriched.append(line)
    return enriched


@dataclass
class _TrackedFileState:
    original: list[str]
    current: list[str]
    history: list[list[str]] = field(default_factory=list)
    created: bool = False


class _FileStateTracker:
    """Replay file_editor operations against observed file contents."""

    def __init__(self) -> None:
        self._files: dict[str, _TrackedFileState] = {}

    def record_view(self, path: str, observation: str) -> None:
        rel_path = _normalize_patch_path(path)
        if rel_path in self._files:
            return
        lines = _parse_numbered_file_lines(observation)
        if lines:
            self._files[rel_path] = _TrackedFileState(
                original=list(lines),
                current=list(lines),
            )

    def record_create(self, path: str, file_text: str) -> None:
        rel_path = _normalize_patch_path(path)
        current = _split_patch_lines(file_text)
        self._files[rel_path] = _TrackedFileState(
            original=[],
            current=current,
            created=True,
        )

    def apply_str_replace(
        self, path: str, old_text: str, new_text: str, observation: str
    ) -> bool:
        if not _is_successful_file_edit_observation(observation):
            return False
        rel_path = _normalize_patch_path(path)
        state = self._files.get(rel_path)
        if state is None:
            return False

        current_text = "\n".join(state.current)
        if current_text.count(old_text) != 1:
            return False
        state.history.append(list(state.current))
        updated = current_text.replace(old_text, new_text, 1)
        state.current = _split_patch_lines(updated)
        return True

    def apply_insert(
        self, path: str, insert_line, new_text: str, observation: str
    ) -> bool:
        if not _is_successful_file_edit_observation(observation):
            return False
        rel_path = _normalize_patch_path(path)
        state = self._files.get(rel_path)
        if state is None:
            return False
        try:
            index = int(insert_line)
        except (TypeError, ValueError):
            return False
        index = max(0, min(index, len(state.current)))
        state.history.append(list(state.current))
        state.current[index:index] = _split_patch_lines(new_text)
        return True

    def undo(self, path: str) -> bool:
        rel_path = _normalize_patch_path(path)
        state = self._files.get(rel_path)
        if state is None or not state.history:
            return False
        state.current = state.history.pop()
        return True

    def modified_paths(self) -> set[str]:
        """Return paths whose tracked final state differs from the original state."""
        return {
            rel_path
            for rel_path, state in self._files.items()
            if state.original != state.current
        }

    def generate_patch(self) -> str:
        patches = []
        for rel_path in sorted(self._files):
            state = self._files[rel_path]
            if state.original == state.current:
                continue
            fromfile = "/dev/null" if state.created else f"a/{rel_path}"
            diff_lines = list(
                difflib.unified_diff(
                    state.original,
                    state.current,
                    fromfile=fromfile,
                    tofile=f"b/{rel_path}",
                    lineterm="",
                    n=3,
                )
            )
            if not diff_lines:
                continue
            diff_lines = _add_hunk_context(diff_lines, state.original, state.current)
            lines = [f"diff --git a/{rel_path} b/{rel_path}"]
            if state.created:
                lines.append("new file mode 100644")
            lines.extend(diff_lines)
            patches.append("\n".join(lines))
        return "\n\n".join(patches).strip()


def _extract_response_message(record: dict) -> dict:
    choices = (record.get("response_body") or {}).get("choices") or []
    if not choices:
        return {}
    message = choices[0].get("message") or {}
    return message if isinstance(message, dict) else {}


def _collect_tool_observations(records: list[dict]) -> dict[str, str]:
    """Collect tool-call observations from the growing request histories."""
    observations = {}
    for record in records:
        request_body = record.get("request_body") or {}
        for message in request_body.get("messages") or []:
            if message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id") or ""
            if call_id and call_id not in observations:
                observations[call_id] = _text_from_content(message.get("content"))
    return observations


def _extract_instruction(records: list[dict]) -> str:
    if not records:
        return ""
    request_body = records[0].get("request_body") or {}
    for message in request_body.get("messages") or []:
        if message.get("role") == "user":
            return _text_from_content(message.get("content"))[:4000]
    return ""


def _load_trial_max_iterations(trial_dir: Path, default: int) -> int:
    config_path = trial_dir / "config.json"
    if not config_path.exists():
        return default
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        value = config.get("agent", {}).get("kwargs", {}).get("max_iterations", default)
        return int(value) if value is not None else default
    except Exception:
        logger.debug(
            "Could not load max_iterations from %s", config_path, exc_info=True
        )
        return default


def parse_litellm_trajectory(
    records: list[dict],
    instance_id: str,
    trial_dir: Path,
    max_iterations_default: int = 0,
) -> Trajectory:
    """Parse one Harbor LiteLLM JSONL file into a Trajectory."""
    observations = _collect_tool_observations(records)
    traj = Trajectory(
        instance_id=instance_id,
        max_iterations=_load_trial_max_iterations(trial_dir, max_iterations_default),
        instruction=_extract_instruction(records),
        metadata={
            "trial_name": trial_dir.name,
            "trajectory_layout": "harbor_litellm",
            "model_patch_source": "reconstructed_from_file_editor",
        },
    )

    step_counter = 0
    seen_tool_calls = set()
    edits = []
    undone_edits = 0
    failures = []
    state_tracker = _FileStateTracker()

    for record in records:
        if not record.get("success", True):
            failure = record.get("failure") or {}
            failures.append(str(failure)[:1000])

        message = _extract_response_message(record)
        thought = _text_from_content(message.get("content"))[:2000]
        reasoning = str(message.get("reasoning_content") or "")[:2000]
        tool_calls = message.get("tool_calls") or []

        if not tool_calls and thought:
            finish_reason = (
                (record.get("response_body") or {}).get("choices") or [{}]
            )[0].get("finish_reason")
            if finish_reason == "stop":
                traj.steps.append(
                    TrajectoryStep(
                        step_id=step_counter,
                        timestamp=record.get("timestamp", ""),
                        action_type="finish",
                        action_detail=thought[:500],
                        observation="",
                        thought=thought,
                        reasoning_content=reasoning,
                        is_error=False,
                        raw_kind="LiteLLMMessage",
                    )
                )
                step_counter += 1
            continue

        for tool_call in tool_calls:
            call_id = _tool_call_id(tool_call)
            if call_id and call_id in seen_tool_calls:
                continue
            if call_id:
                seen_tool_calls.add(call_id)

            function = tool_call.get("function") or {}
            tool_name = function.get("name") or tool_call.get("function_name") or ""
            arguments = _parse_tool_arguments(
                function.get("arguments") or tool_call.get("arguments")
            )
            action_type, action_detail = _classify_litellm_tool_call(
                tool_name, arguments
            )
            observation = observations.get(call_id, "") if call_id else ""

            if tool_name == "file_editor":
                command = arguments.get("command")
                path = str(arguments.get("path", ""))
                if command == "view":
                    if not arguments.get("view_range"):
                        state_tracker.record_view(path, observation)
                elif command in ("str_replace", "create", "insert"):
                    edits.append(arguments)
                    if command == "create":
                        state_tracker.record_create(
                            path, str(arguments.get("file_text", ""))
                        )
                    elif command == "str_replace":
                        state_tracker.apply_str_replace(
                            path,
                            str(arguments.get("old_str", "")),
                            str(arguments.get("new_str", "")),
                            observation,
                        )
                    elif command == "insert":
                        state_tracker.apply_insert(
                            path,
                            arguments.get("insert_line"),
                            str(arguments.get("new_str", "")),
                            observation,
                        )
                elif command == "undo_edit":
                    path = arguments.get("path")
                    state_undone = state_tracker.undo(str(path or ""))
                    edit_undone = False
                    for idx in range(len(edits) - 1, -1, -1):
                        if edits[idx].get("path") == path:
                            edits.pop(idx)
                            edit_undone = True
                            break
                    if state_undone or edit_undone:
                        undone_edits += 1

            traj.steps.append(
                TrajectoryStep(
                    step_id=step_counter,
                    timestamp=record.get("timestamp", ""),
                    action_type=action_type,
                    action_detail=action_detail,
                    observation=observation,
                    thought=thought,
                    reasoning_content=reasoning,
                    is_error=not record.get("success", True)
                    or _is_error_text(observation),
                    raw_kind="LiteLLMToolCall",
                )
            )
            step_counter += 1

    full_state_patch = state_tracker.generate_patch()
    if full_state_patch:
        fallback_patch = _reconstruct_patch_from_edits(
            edits,
            exclude_paths=state_tracker.modified_paths(),
        )
        traj.model_patch = "\n\n".join(
            patch for patch in (full_state_patch, fallback_patch) if patch
        )
        patch_source = "reconstructed_from_file_editor_full_state"
        if fallback_patch:
            patch_source = "reconstructed_from_file_editor_hybrid"
    else:
        traj.model_patch = _reconstruct_patch_from_edits(edits)
        patch_source = "reconstructed_from_file_editor_snippet_fallback"
    traj.error = "\n".join(failures) if failures and not traj.model_patch else None
    traj.metadata.update(
        {
            "model_patch_source": patch_source,
            "model_patch_edit_count": len(edits),
            "model_patch_undo_count": undone_edits,
            "model_patch_files": sorted(
                {
                    _normalize_patch_path(str(edit.get("path", "")))
                    for edit in edits
                    if edit.get("path")
                }
            ),
        }
    )
    return traj


def parse_trajectory(entry: dict) -> Trajectory:
    """Parse a single JSONL entry into a Trajectory."""
    traj = Trajectory(
        instance_id=entry["instance_id"],
        max_iterations=entry.get("metadata", {}).get("max_iterations", 0),
        model_patch=entry.get("test_result", {}).get("git_patch", ""),
        instruction=entry.get("instruction", ""),
        error=entry.get("error"),
        metadata=entry.get("metadata", {}),
    )

    history = entry.get("history", [])
    step_counter = 0

    # We pair ActionEvents with their subsequent ObservationEvents
    pending_observation = ""
    pending_is_error = False

    for event in history:
        kind = event.get("kind", "")

        if kind == "ObservationEvent":
            pending_observation = _extract_observation(event)
            pending_is_error = _is_error_observation(event)
            continue

        if kind == "ActionEvent":
            action_type, action_detail = _classify_action(event)
            thought = _extract_thought(event)
            reasoning = event.get("reasoning_content", "") or ""

            step = TrajectoryStep(
                step_id=step_counter,
                timestamp=event.get("timestamp", ""),
                action_type=action_type,
                action_detail=action_detail,
                observation=pending_observation,
                thought=thought,
                reasoning_content=reasoning[:2000],
                is_error=pending_is_error,
                raw_kind=kind,
            )
            traj.steps.append(step)
            step_counter += 1

            # Reset pending observation
            pending_observation = ""
            pending_is_error = False

    return traj


def load_trajectories(trajectory_path: Path) -> dict[str, Trajectory]:
    """Load all trajectories from JSONL file.

    Returns dict mapping instance_id -> Trajectory.
    """
    trajectories = {}
    with open(trajectory_path, "r") as f:
        for line in f:
            entry = json.loads(line)
            traj = parse_trajectory(entry)
            trajectories[traj.instance_id] = traj
    logger.info("Loaded %d trajectories from %s", len(trajectories), trajectory_path)
    return trajectories


def _default_harbor_loader_workers(num_trials: int) -> int:
    if num_trials <= 1:
        return 1
    cpu_count = os.cpu_count() or 1
    return min(num_trials, max(1, min(16, cpu_count)))


def _load_harbor_trial_trajectory(
    trial_dir: Path,
    *,
    trajectory_subpath: str,
    max_iterations_default: int,
    trial_result_file: str,
) -> tuple[str, Trajectory] | None:
    trajectory_path = trial_dir / trajectory_subpath
    if not trajectory_path.exists():
        return None

    records = []
    with open(trajectory_path, "r") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(
                    "Skipping malformed JSON in %s:%d: %s", trajectory_path, line_no, e
                )

    instance_id = resolve_instance_id(trial_dir, trial_result_file=trial_result_file)
    trajectory = parse_litellm_trajectory(
        records,
        instance_id=instance_id,
        trial_dir=trial_dir,
        max_iterations_default=max_iterations_default,
    )
    return instance_id, trajectory


def _load_harbor_trial_trajectory_from_args(
    args: tuple[Path, str, int, str],
) -> tuple[str, Trajectory] | None:
    trial_dir, trajectory_subpath, max_iterations_default, trial_result_file = args
    return _load_harbor_trial_trajectory(
        trial_dir,
        trajectory_subpath=trajectory_subpath,
        max_iterations_default=max_iterations_default,
        trial_result_file=trial_result_file,
    )


def load_harbor_trajectories(
    job_dir: Path,
    trajectory_subpath: str = "agent/litellm-trajectory.jsonl",
    max_iterations_default: int = 0,
    trial_result_file: str = "result.json",
    max_workers: int | None = None,
) -> dict[str, Trajectory]:
    """Load Harbor per-trial LiteLLM trajectories from a job directory."""
    trajectories = {}
    if not job_dir.exists():
        raise FileNotFoundError(f"Harbor job dir not found: {job_dir}")

    trial_dirs = sorted(p for p in job_dir.iterdir() if p.is_dir())
    worker_count = max_workers or _default_harbor_loader_workers(len(trial_dirs))

    task_args = [
        (trial_dir, trajectory_subpath, max_iterations_default, trial_result_file)
        for trial_dir in trial_dirs
    ]

    if worker_count <= 1:
        progress = Progress("Loading Harbor trajectories", len(trial_dirs))
        try:
            for args in task_args:
                loaded = _load_harbor_trial_trajectory_from_args(args)
                progress.update()
                if loaded is not None:
                    instance_id, trajectory = loaded
                    trajectories[instance_id] = trajectory
        finally:
            progress.close()
    else:
        progress = Progress("Loading Harbor trajectories", len(trial_dirs))
        try:
            with ProcessPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_load_harbor_trial_trajectory_from_args, args): idx
                    for idx, args in enumerate(task_args)
                }
                loaded_results = [None] * len(task_args)
                for future in as_completed(futures):
                    loaded_results[futures[future]] = future.result()
                    progress.update()

            for loaded in loaded_results:
                if loaded is not None:
                    instance_id, trajectory = loaded
                    trajectories[instance_id] = trajectory
        finally:
            progress.close()

    logger.info(
        "Loaded %d Harbor trajectories from %s (workers=%d)",
        len(trajectories),
        job_dir,
        worker_count,
    )
    return trajectories
