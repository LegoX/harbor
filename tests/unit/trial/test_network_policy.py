import pytest

from harbor.models.task.config import NetworkMode, NetworkPolicy, TaskConfig
from harbor.models.trial.config import AgentConfig as TrialAgentConfig
from harbor.trial.network_policy import (
    merge_extra_allowlists,
    requires_mutable_network_after_start,
    resolve_agent_phase_policy,
    resolve_verifier_phase_policy,
    should_apply_agent_phase_policy,
    should_apply_environment_baseline,
    should_apply_legacy_baseline_for_dynamic_policy,
    should_apply_verifier_phase_policy,
)


def test_merge_extra_allowlists_converts_no_network_to_allowlist() -> None:
    policy = merge_extra_allowlists(
        NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
        ["API.OpenAI.com.", "api.openai.com"],
    )

    assert policy == NetworkPolicy(
        network_mode=NetworkMode.ALLOWLIST,
        allowed_hosts=["api.openai.com"],
    )


def test_merge_extra_allowlists_warns_when_policy_is_public() -> None:
    public = NetworkPolicy(network_mode=NetworkMode.PUBLIC)

    with pytest.warns(UserWarning, match="ignored"):
        assert merge_extra_allowlists(public, ["api.openai.com"]) == public


def test_resolve_agent_phase_policy_prefers_task_agent_policy() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
network_mode = "no-network"

[agent]
network_mode = "allowlist"
allowed_hosts = ["api.openai.com"]
""")

    policy = resolve_agent_phase_policy(
        task_config,
        TrialAgentConfig(name="codex", extra_allowed_hosts=["proxy.local"]),
        task_config.environment.resolve_baseline(),
    )

    assert policy == NetworkPolicy(
        network_mode=NetworkMode.ALLOWLIST,
        allowed_hosts=["api.openai.com", "proxy.local"],
    )


def test_resolve_verifier_phase_policy_prefers_task_verifier_policy() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
network_mode = "public"

[verifier]
network_mode = "no-network"
""")

    assert resolve_verifier_phase_policy(
        task_config, task_config.environment.resolve_baseline()
    ) == NetworkPolicy(network_mode=NetworkMode.NO_NETWORK)


def test_legacy_allow_internet_false_does_not_require_dynamic_phase_apply() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
allow_internet = false
""")
    trial_agent_config = TrialAgentConfig(name="codex")

    assert not should_apply_environment_baseline(task_config)
    assert not should_apply_agent_phase_policy(task_config, trial_agent_config)
    assert not should_apply_verifier_phase_policy(task_config, trial_agent_config)


def test_agent_policy_requires_verifier_reset_to_baseline() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
network_mode = "public"

[agent]
network_mode = "allowlist"
allowed_hosts = ["api.openai.com"]
""")
    trial_agent_config = TrialAgentConfig(name="codex")

    assert should_apply_environment_baseline(task_config)
    assert should_apply_agent_phase_policy(task_config, trial_agent_config)
    assert should_apply_verifier_phase_policy(task_config, trial_agent_config)


def test_legacy_no_network_with_extra_allowlist_requires_mutable_network() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
allow_internet = false
""")
    trial_agent_config = TrialAgentConfig(
        name="codex", extra_allowed_hosts=["api.openai.com"]
    )

    assert requires_mutable_network_after_start(task_config, trial_agent_config)
    assert should_apply_legacy_baseline_for_dynamic_policy(
        task_config, trial_agent_config
    )


def test_legacy_no_network_with_verifier_no_network_can_use_static_overlay() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
allow_internet = false

[verifier]
network_mode = "no-network"
""")
    trial_agent_config = TrialAgentConfig(name="codex")

    assert not requires_mutable_network_after_start(task_config, trial_agent_config)
    assert not should_apply_legacy_baseline_for_dynamic_policy(
        task_config, trial_agent_config
    )
    assert not should_apply_verifier_phase_policy(task_config, trial_agent_config)


def test_agent_policy_still_requires_verifier_reset_to_static_baseline() -> None:
    task_config = TaskConfig.model_validate_toml("""
[environment]
allow_internet = false

[agent]
network_mode = "allowlist"
allowed_hosts = ["api.openai.com"]

[verifier]
network_mode = "no-network"
""")
    trial_agent_config = TrialAgentConfig(name="codex")

    assert should_apply_verifier_phase_policy(task_config, trial_agent_config)
