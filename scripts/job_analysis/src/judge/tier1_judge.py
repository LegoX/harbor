"""Tier-1 LLM Judge: structured multi-dimensional scoring."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.parser.trajectory_parser import Trajectory

logger = logging.getLogger(__name__)


@dataclass
class Tier1Judgment:
    """Structured judgment from the LLM judge."""

    localization_quality: str  # hit, partial, miss
    behavioral_understanding: str  # correct, partial, wrong
    implementation_quality: (
        str  # correct, partial_fix, wrong_logic, wrong_location, no_edit
    )
    tool_usage_health: str  # ok, suboptimal, poor
    failure_attribution: str  # localization, diagnosis, implementation, tool_usage, long_horizon, environment
    evidence_step_ids: list[int] = field(default_factory=list)
    reasoning: str = ""
    raw_response: str = ""


# Valid values for each dimension
VALID_VALUES = {
    "localization_quality": {"hit", "partial", "miss"},
    "behavioral_understanding": {"correct", "partial", "wrong"},
    "implementation_quality": {
        "correct",
        "partial_fix",
        "wrong_logic",
        "wrong_location",
        "no_edit",
    },
    "tool_usage_health": {"ok", "suboptimal", "poor"},
    "failure_attribution": {
        "localization",
        "diagnosis",
        "implementation",
        "tool_usage",
        "long_horizon",
        "environment",
    },
}

JUDGE_SYSTEM_PROMPT = """You are an expert software engineering analyst. You analyze failed SWE-bench evaluation attempts to determine why the model failed.

You must output a JSON object with exactly these fields:
- localization_quality: "hit" | "partial" | "miss" — did the agent find the right file/function?
- behavioral_understanding: "correct" | "partial" | "wrong" — did the agent understand the bug correctly?
- implementation_quality: "correct" | "partial_fix" | "wrong_logic" | "wrong_location" | "no_edit" — quality of the code change
- tool_usage_health: "ok" | "suboptimal" | "poor" — was tool usage effective?
- failure_attribution: "localization" | "diagnosis" | "implementation" | "tool_usage" | "long_horizon" | "environment" — primary failure cause
- evidence_step_ids: [int] — step IDs that provide evidence for your judgment
- reasoning: string — brief explanation of your judgment

Output ONLY valid JSON, no other text."""


def _build_judge_prompt(
    trajectory: Trajectory,
    model_patch: str,
    gold_patch: str,
    deterministic_features: dict,
    max_trajectory_chars: int = 8000,
    resolved: bool = False,
) -> str:
    """Build the judge prompt from trajectory and feature data."""
    # Summarize trajectory
    step_summaries = []
    total_chars = 0
    for step in trajectory.steps:
        summary = (
            f"Step {step.step_id} [{step.action_type}]: {step.action_detail[:200]}"
        )
        if step.is_error:
            summary += " [ERROR]"
        if step.thought:
            summary += f" | Thought: {step.thought[:150]}"
        step_summaries.append(summary)
        total_chars += len(summary)
        if total_chars > max_trajectory_chars:
            step_summaries.append(
                f"... (truncated, {len(trajectory.steps) - step.step_id - 1} more steps)"
            )
            break

    trajectory_summary = "\n".join(step_summaries)

    # Format deterministic features
    features_str = "\n".join(f"  {k}: {v}" for k, v in deterministic_features.items())

    # Truncate patches
    model_patch_display = model_patch[:2000] if model_patch else "(empty)"
    gold_patch_display = gold_patch[:2000] if gold_patch else "(empty)"

    outcome = "resolved" if resolved else "failed"
    prompt = f"""## Instance: {trajectory.instance_id}
## Outcome: {outcome}

## Trajectory Summary
{trajectory_summary}

## Model Patch
```diff
{model_patch_display}
```

## Gold Patch
```diff
{gold_patch_display}
```

## Deterministic Features
{features_str}

Analyze this {outcome} attempt and provide your structured judgment as JSON."""

    return prompt


def _call_llm(prompt: str, system_prompt: str, cfg) -> Optional[str]:
    """Call LLM API. Supports Anthropic API."""
    if not cfg.judge.api_key:
        logger.warning("No API key configured for judge. Skipping LLM call.")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=cfg.judge.api_key,
            base_url=cfg.judge.api_base,
        )

        response = client.messages.create(
            model=cfg.judge.model,
            max_tokens=cfg.judge.max_tokens,
            temperature=cfg.judge.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text

    except ImportError:
        logger.error(
            "anthropic package not installed. Install with: pip install anthropic"
        )
        return None
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return None


def _parse_judge_response(response: str) -> Tier1Judgment:
    """Parse LLM response into Tier1Judgment."""
    judgment = Tier1Judgment(
        localization_quality="miss",
        behavioral_understanding="wrong",
        implementation_quality="no_edit",
        tool_usage_health="ok",
        failure_attribution="implementation",
        raw_response=response,
    )

    # Try to extract JSON from response
    try:
        # Find JSON object in response
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(response[start:end])
        else:
            logger.warning("No JSON found in judge response")
            return judgment
    except json.JSONDecodeError:
        logger.warning("Failed to parse judge response as JSON")
        return judgment

    # Validate and set fields
    for dim, valid in VALID_VALUES.items():
        val = data.get(dim, "")
        if val in valid:
            setattr(judgment, dim, val)
        else:
            logger.warning(
                "Invalid value for %s: %s (expected one of %s)", dim, val, valid
            )

    judgment.evidence_step_ids = data.get("evidence_step_ids", [])
    judgment.reasoning = data.get("reasoning", "")

    return judgment


def judge_instance(
    trajectory: Trajectory,
    model_patch: str,
    gold_patch: str,
    deterministic_features: dict,
    cfg,
    resolved: bool = False,
) -> Tier1Judgment:
    """Run tier-1 judge on a single instance."""
    if not cfg.judge.enabled:
        # Return default judgment based on deterministic features only
        return _default_judgment(deterministic_features, resolved=resolved)

    prompt = _build_judge_prompt(
        trajectory,
        model_patch,
        gold_patch,
        deterministic_features,
        cfg.judge.max_trajectory_chars,
        resolved=resolved,
    )

    response = _call_llm(prompt, JUDGE_SYSTEM_PROMPT, cfg)
    if response is None:
        return _default_judgment(deterministic_features, resolved=resolved)

    return _parse_judge_response(response)


def _default_judgment(features: dict, resolved: bool = False) -> Tier1Judgment:
    """Generate a heuristic judgment from deterministic features when LLM is disabled."""
    # Localization
    if features.get("C1_file_read") and features.get("C2_func_read"):
        loc = "hit"
    elif features.get("C1_file_read"):
        loc = "partial"
    else:
        loc = "miss"

    # Behavioral understanding - infer from localization + implementation alignment
    # If agent found the right file AND function, likely understood the problem at least partially
    # If agent found right file but wrong function, partial understanding
    # If agent didn't find right file at all, likely wrong understanding
    if resolved and features.get("diff_hunk_overlap", 0) < 0.5:
        behav = "correct"
    elif (
        loc == "hit"
        and features.get("C3_file_alignment")
        and features.get("C4_func_alignment")
    ):
        behav = "partial" if not resolved else "correct"
    elif loc == "hit" and features.get("C3_file_alignment"):
        behav = "partial"  # Found right place but may have misunderstood the fix
    elif loc == "partial":
        behav = "partial"
    else:
        behav = "wrong"  # Didn't find the right location

    # Implementation
    if not features.get("model_patch_exists", True):
        impl = "no_edit"
    elif resolved:
        impl = "correct"
    elif features.get("C3_file_alignment") and features.get("C4_func_alignment"):
        # High hunk overlap suggests correct implementation
        if features.get("diff_hunk_overlap", 0) >= 0.5:
            impl = "partial_fix"  # Close to gold but still failing
        else:
            impl = "partial_fix"
    elif features.get("C3_file_alignment"):
        impl = "wrong_logic"
    else:
        impl = "wrong_location"

    # Tool usage
    if features.get("tool_error_storm"):
        tool = "poor"
    elif features.get("loop_detected"):
        tool = "suboptimal"
    else:
        tool = "ok"

    # Failure attribution: earliest causal
    if loc == "miss":
        attr = "localization"
    elif behav == "wrong":
        attr = "diagnosis"
    elif impl in ("wrong_logic", "wrong_location", "no_edit"):
        attr = "implementation"
    elif tool != "ok":
        attr = "tool_usage"
    elif features.get("premature_stop") or features.get("context_truncation"):
        attr = "long_horizon"
    else:
        attr = "implementation"

    return Tier1Judgment(
        localization_quality=loc,
        behavioral_understanding=behav,
        implementation_quality=impl,
        tool_usage_health=tool,
        failure_attribution=attr,
        reasoning="Heuristic judgment (LLM judge disabled)",
    )
