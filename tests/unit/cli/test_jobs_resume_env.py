import pytest

from harbor.cli.jobs import apply_resume_agent_env_defaults
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig


def test_resume_agent_env_defaults_do_not_override_explicit_values(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "host-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://host.example/v1")
    config = JobConfig(
        agents=[
            AgentConfig(
                name="oracle",
                env={
                    "LLM_BASE_URL": "https://explicit.example/v1",
                    "OTHER": "value",
                },
            )
        ]
    )

    apply_resume_agent_env_defaults(config)

    assert config.agents[0].env == {
        "LLM_API_KEY": "host-key",
        "LLM_BASE_URL": "https://explicit.example/v1",
        "OTHER": "value",
    }


def test_resume_agent_env_defaults_restore_masked_values_in_memory(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-host-key")
    config = JobConfig(
        agents=[
            AgentConfig(
                name="oracle",
                env={"ANTHROPIC_API_KEY": "abcd****xyz", "OTHER": "value"},
            )
        ]
    )

    apply_resume_agent_env_defaults(config)

    assert config.agents[0].env["ANTHROPIC_API_KEY"] == "real-host-key"
    assert config.agents[0].env["OTHER"] == "value"


def test_resume_agent_env_defaults_reject_unresolved_masked_values(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = JobConfig(
        agents=[AgentConfig(name="oracle", env={"ANTHROPIC_API_KEY": "****"})]
    )

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        apply_resume_agent_env_defaults(config)


def test_resume_agent_env_defaults_can_allow_unresolved_masked_values(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = JobConfig(
        agents=[AgentConfig(name="oracle", env={"ANTHROPIC_API_KEY": "****"})]
    )

    apply_resume_agent_env_defaults(config, allow_masked_secrets=True)

    assert config.agents[0].env["ANTHROPIC_API_KEY"] == "****"
