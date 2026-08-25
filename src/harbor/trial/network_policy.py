"""Phase-scoped network policy resolution for trials."""

from __future__ import annotations

import warnings

from harbor.models.task.config import (
    NetworkMode,
    NetworkPolicy,
    TaskConfig,
    normalize_allowed_hosts,
)
from harbor.models.trial.config import AgentConfig as TrialAgentConfig


def merge_extra_allowlists(
    policy: NetworkPolicy, extra_allowed_hosts: list[str]
) -> NetworkPolicy:
    if not extra_allowed_hosts:
        return policy
    if policy.network_mode == NetworkMode.PUBLIC:
        warnings.warn(
            "Run-specific allowlist host(s) "
            f"{extra_allowed_hosts!r} are ignored because the effective network "
            "policy is public.",
            UserWarning,
            stacklevel=3,
        )
        return policy
    allowed_hosts = list(
        dict.fromkeys(
            [*policy.allowed_hosts, *normalize_allowed_hosts(extra_allowed_hosts)]
        )
    )
    return NetworkPolicy(
        network_mode=NetworkMode.ALLOWLIST, allowed_hosts=allowed_hosts
    )


def resolve_agent_phase_policy(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
    agent_env_baseline: NetworkPolicy,
) -> NetworkPolicy:
    policy = task_cfg.agent.explicit_phase_policy() or agent_env_baseline
    return merge_extra_allowlists(policy, list(trial_agent_cfg.extra_allowed_hosts))


def resolve_verifier_phase_policy(
    task_cfg: TaskConfig,
    baseline: NetworkPolicy,
) -> NetworkPolicy:
    return task_cfg.verifier.explicit_phase_policy() or baseline


def should_apply_environment_baseline(task_cfg: TaskConfig) -> bool:
    return task_cfg.environment.network_mode is not None


def requires_mutable_network_after_start(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
) -> bool:
    baseline = task_cfg.environment.resolve_baseline()
    if baseline.network_mode != NetworkMode.NO_NETWORK:
        return False
    if (
        should_apply_agent_phase_policy(task_cfg, trial_agent_cfg)
        and resolve_agent_phase_policy(task_cfg, trial_agent_cfg, baseline).network_mode
        != NetworkMode.NO_NETWORK
    ):
        return True
    return (
        task_cfg.verifier.explicit_phase_policy() is not None
        and resolve_verifier_phase_policy(task_cfg, baseline).network_mode
        != NetworkMode.NO_NETWORK
    )


def should_apply_legacy_baseline_for_dynamic_policy(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
) -> bool:
    return (
        task_cfg.environment.network_mode is None
        and not task_cfg.environment.allow_internet
        and requires_mutable_network_after_start(task_cfg, trial_agent_cfg)
    )


def should_apply_agent_phase_policy(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
) -> bool:
    return task_cfg.agent.explicit_phase_policy() is not None or bool(
        trial_agent_cfg.extra_allowed_hosts
    )


def should_apply_verifier_phase_policy(
    task_cfg: TaskConfig,
    trial_agent_cfg: TrialAgentConfig,
) -> bool:
    should_reset_agent_policy = should_apply_agent_phase_policy(
        task_cfg, trial_agent_cfg
    )
    verifier_policy = task_cfg.verifier.explicit_phase_policy()
    if verifier_policy is None:
        return should_reset_agent_policy
    if should_reset_agent_policy:
        return True
    return verifier_policy != task_cfg.environment.resolve_baseline()
