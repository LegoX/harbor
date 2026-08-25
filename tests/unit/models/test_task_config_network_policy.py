import pytest
from pydantic import ValidationError

from harbor.models.task.config import NetworkMode, NetworkPolicy, TaskConfig


def test_environment_allow_internet_false_maps_to_no_network() -> None:
    config = TaskConfig.model_validate_toml("""
[environment]
allow_internet = false
""")

    assert config.environment.resolve_baseline() == NetworkPolicy(
        network_mode=NetworkMode.NO_NETWORK
    )


def test_environment_network_mode_overrides_allow_internet() -> None:
    config = TaskConfig.model_validate_toml("""
[environment]
allow_internet = false
network_mode = "public"
""")

    assert config.environment.resolve_baseline() == NetworkPolicy(
        network_mode=NetworkMode.PUBLIC
    )


def test_allowed_hosts_imply_allowlist_and_normalize() -> None:
    config = TaskConfig.model_validate_toml("""
[agent]
allowed_hosts = [" API.OpenAI.com. ", "api.openai.com"]
""")

    assert config.agent.explicit_phase_policy() == NetworkPolicy(
        network_mode=NetworkMode.ALLOWLIST,
        allowed_hosts=["api.openai.com"],
    )


def test_allowed_hosts_reject_urls() -> None:
    with pytest.raises(ValidationError, match="not URLs"):
        TaskConfig.model_validate_toml("""
[agent]
network_mode = "allowlist"
allowed_hosts = ["https://api.openai.com"]
""")
