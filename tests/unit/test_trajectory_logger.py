"""Unit tests for the LiteLLM trajectory logger callback."""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


_TRAJECTORY_LOGGER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "serve_llm"
    / "trajectory_logger.py"
)
_SPEC = spec_from_file_location("trajectory_logger_for_tests", _TRAJECTORY_LOGGER_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - defensive import guard
    raise RuntimeError(
        f"Unable to load trajectory logger from {_TRAJECTORY_LOGGER_PATH}"
    )
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

TrajectoryLogger = _MODULE.TrajectoryLogger
_DEFAULT_TOP_P = _MODULE._DEFAULT_TOP_P
_STICKY_ROUTING_ALIASES_ENV = _MODULE._STICKY_ROUTING_ALIASES_ENV
_TEMPERATURE_HEADER = _MODULE._TEMPERATURE_HEADER


def test_init_does_not_create_output_dir(monkeypatch, tmp_path: Path):
    output_dir = tmp_path / "missing-trajectories"
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(output_dir))

    TrajectoryLogger()

    assert not output_dir.exists()


def test_write_record_routes_job_trial_beneath_job_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRAJECTORY_JOB_ROOT", str(tmp_path / "jobs"))
    logger = TrajectoryLogger()
    now = _MODULE.datetime.now()
    record = logger._build_record(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
            "proxy_server_request": {
                "headers": {"x-trajectory-output-path": "job-a/trial-a"}
            },
        },
        None,
        now,
        now,
    )

    logger._write_record(record)

    output_path = (
        tmp_path / "jobs" / "job-a" / "trial-a" / "agent" / "litellm-trajectory.jsonl"
    )
    persisted = json.loads(output_path.read_text())
    assert persisted["trajectory_route"] == "job-a/trial-a"
    assert "_trajectory_output" not in persisted
    assert str(tmp_path) not in output_path.read_text()


@pytest.mark.parametrize("route", ["../trial", "job/../../escape", "job-only"])
def test_trajectory_route_rejects_traversal_and_invalid_shapes(
    monkeypatch, tmp_path: Path, route: str
):
    monkeypatch.setenv("TRAJECTORY_JOB_ROOT", str(tmp_path / "jobs"))
    logger = TrajectoryLogger()

    with pytest.raises(ValueError):
        logger._resolve_trajectory_output(route)


def test_trajectory_route_rejects_absolute_path_outside_allowed_roots(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_JOB_ROOT", str(tmp_path / "jobs"))
    logger = TrajectoryLogger()

    with pytest.raises(ValueError, match="outside allowed roots"):
        logger._resolve_trajectory_output(
            str(tmp_path / "outside" / "litellm-trajectory.jsonl")
        )


def test_fallback_filename_cannot_escape_output_directory(monkeypatch, tmp_path: Path):
    output_dir = tmp_path / "trajectories"
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(output_dir))
    logger = TrajectoryLogger()
    record = {
        "session_id": "../../escape",
        "success": True,
        "request_body": {"model": "test-model"},
        "response_body": {},
        "usage": {},
    }

    logger._write_record(record)

    files = list(output_dir.glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].name.startswith("session-")
    assert not (tmp_path / "escape.jsonl").exists()


def test_extra_params_redact_sensitive_values():
    logger = TrajectoryLogger()

    extra = logger._collect_extra_params(
        {
            "optional_params": {
                "api_key": "sk-sensitive",
                "custom_token": "token-sensitive",
                "seed": 42,
            }
        }
    )

    assert extra == {
        "api_key": "[REDACTED]",
        "custom_token": "[REDACTED]",
        "seed": 42,
    }


@pytest.mark.asyncio
async def test_pre_call_hook_applies_temperature_from_proxy_header(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    logger = TrajectoryLogger()

    data = {
        "messages": [{"role": "user", "content": "hi"}],
        "proxy_server_request": {"headers": {_TEMPERATURE_HEADER: "0.35"}},
    }

    result = await logger.async_pre_call_hook(None, None, data, "completion")

    assert result["temperature"] == 0.35
    assert result["top_p"] == _DEFAULT_TOP_P


@pytest.mark.asyncio
async def test_pre_call_hook_preserves_explicit_temperature(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    logger = TrajectoryLogger()

    data = {
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.1,
        "optional_params": {"temperature": 0.1},
        "proxy_server_request": {"headers": {_TEMPERATURE_HEADER: "0.35"}},
    }

    result = await logger.async_pre_call_hook(None, None, data, "completion")

    assert result["temperature"] == 0.1
    assert result["optional_params"]["temperature"] == 0.1
    assert result["top_p"] == _DEFAULT_TOP_P


@pytest.mark.asyncio
async def test_pre_call_hook_preserves_explicit_top_p(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    logger = TrajectoryLogger()

    data = {
        "messages": [{"role": "user", "content": "hi"}],
        "top_p": 0.7,
        "optional_params": {"top_p": 0.8},
    }

    result = await logger.async_pre_call_hook(None, None, data, "completion")

    assert result["top_p"] == 0.7
    assert result["optional_params"]["top_p"] == 0.8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "openai/claude-opus-4-6-aws",
        "openai/claude-opus-4-8-aws",
    ],
)
async def test_pre_call_hook_removes_top_p_for_aws_aliases(
    monkeypatch, tmp_path: Path, model: str
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    logger = TrajectoryLogger()

    data = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "top_p": 0.7,
        "optional_params": {"top_p": 0.8},
    }

    result = await logger.async_pre_call_hook(None, None, data, "completion")

    assert "top_p" not in result
    assert "top_p" not in result["optional_params"]


def _build_logged_request_body(logger: TrajectoryLogger, kwargs: dict) -> dict:
    now = _MODULE.datetime.now()
    record = logger._build_record(kwargs, None, now, now)
    return record["request_body"]


def test_build_record_omits_top_p_when_absent():
    logger = TrajectoryLogger()

    request_body = _build_logged_request_body(
        logger,
        {
            "model": "claude-opus-4-6",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert "top_p" not in request_body


def test_build_record_preserves_default_injected_top_p():
    logger = TrajectoryLogger()
    request = logger._apply_sampling_defaults(
        {
            "model": "other-model",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    request_body = _build_logged_request_body(logger, request)

    assert request_body["top_p"] == _DEFAULT_TOP_P


def test_build_record_preserves_explicit_optional_top_p():
    logger = TrajectoryLogger()

    request_body = _build_logged_request_body(
        logger,
        {
            "model": "other-model",
            "messages": [{"role": "user", "content": "hi"}],
            "optional_params": {"top_p": 0.7},
        },
    )

    assert request_body["top_p"] == 0.7


@pytest.mark.asyncio
async def test_pre_call_hook_routes_same_task_suffix_to_same_backend(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.1-FP8=GLM-5.1-FP8@llm,GLM-5.1-FP8@llm3,GLM-5.1-FP8@llm30",
    )
    logger = TrajectoryLogger()

    first_path = (
        tmp_path
        / "jobs"
        / "job"
        / "owner__repo-123__abc1234"
        / "agent"
        / "litellm-trajectory.jsonl"
    )
    second_path = (
        tmp_path
        / "jobs"
        / "job"
        / "owner__repo-123__xyz9876"
        / "agent"
        / "litellm-trajectory.jsonl"
    )

    first_result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "GLM-5.1-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {
                "headers": {"x-trajectory-output-path": str(first_path)}
            },
        },
        "completion",
    )
    second_result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "GLM-5.1-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {
                "headers": {"x-trajectory-output-path": str(second_path)}
            },
        },
        "completion",
    )

    assert first_result["model"] == second_result["model"]
    assert first_result["model"].startswith("GLM-5.1-FP8@")
    assert first_result["metadata"]["sticky_routing_key"] == "owner__repo-123"
    assert first_result["metadata"]["original_model"] == "GLM-5.1-FP8"


@pytest.mark.asyncio
async def test_pre_call_hook_routes_from_metadata_sticky_key(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.2-FP8=GLM-5.2-FP8@llm50,GLM-5.2-FP8@llm51,GLM-5.2-FP8@llm52",
    )
    logger = TrajectoryLogger()

    result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "GLM-5.2-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"sticky_routing_key": "owner__repo-123"},
        },
        "completion",
    )

    assert result["model"].startswith("GLM-5.2-FP8@llm")
    assert result["metadata"]["sticky_routing_key"] == "owner__repo-123"
    assert result["metadata"]["sticky_routed_model"] == result["model"]
    assert result["metadata"]["original_model"] == "GLM-5.2-FP8"


@pytest.mark.asyncio
async def test_pre_call_hook_routes_from_litellm_routing_header(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.2-FP8=GLM-5.2-FP8@llm50,GLM-5.2-FP8@llm51,GLM-5.2-FP8@llm52",
    )
    logger = TrajectoryLogger()

    result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "GLM-5.2-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {
                "headers": {"x-litellm-routing-key": "owner__repo-123"}
            },
        },
        "completion",
    )

    assert result["model"].startswith("GLM-5.2-FP8@llm")
    assert result["metadata"]["sticky_routing_key"] == "owner__repo-123"
    assert result["metadata"]["sticky_routed_model"] == result["model"]


@pytest.mark.asyncio
async def test_pre_call_hook_routes_from_instance_id_header(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.2-FP8=GLM-5.2-FP8@llm50,GLM-5.2-FP8@llm51,GLM-5.2-FP8@llm52",
    )
    logger = TrajectoryLogger()

    result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "GLM-5.2-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {"headers": {"x-instance-id": "owner__repo-123"}},
        },
        "completion",
    )

    assert result["model"].startswith("GLM-5.2-FP8@llm")
    assert result["metadata"]["sticky_routing_key"] == "owner__repo-123"
    assert result["metadata"]["sticky_routed_model"] == result["model"]


@pytest.mark.asyncio
async def test_pre_call_hook_routes_provider_prefixed_model_group(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.2-FP8=GLM-5.2-FP8@llm50,GLM-5.2-FP8@llm51",
    )
    logger = TrajectoryLogger()

    result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "openai/GLM-5.2-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {
                "headers": {"x-litellm-routing-key": "owner__repo-123"}
            },
        },
        "completion",
    )

    assert result["model"].startswith("GLM-5.2-FP8@llm")
    assert result["metadata"]["original_model"] == "openai/GLM-5.2-FP8"


@pytest.mark.asyncio
async def test_pre_call_hook_falls_back_from_cooled_down_sticky_alias(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("TRAJECTORY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.1-FP8=GLM-5.1-FP8@llm,GLM-5.1-FP8@llm3,GLM-5.1-FP8@llm30",
    )
    logger = TrajectoryLogger()
    routing_key = "owner__repo-123"
    aliases = logger._parse_sticky_routing_aliases()["GLM-5.1-FP8"]
    primary_alias, _, _ = logger._select_sticky_alias(routing_key, aliases)

    with logger._sticky_state_lock:
        logger._sticky_alias_cooldown_until[primary_alias] = (
            _MODULE.time.monotonic() + 60
        )

    result = await logger.async_pre_call_hook(
        None,
        None,
        {
            "model": "GLM-5.1-FP8",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {"headers": {"x-litellm-routing-key": routing_key}},
        },
        "completion",
    )

    assert result["model"] != primary_alias
    assert result["model"] in aliases
    assert result["metadata"]["sticky_primary_model"] == primary_alias
    assert result["metadata"]["sticky_routing_fallback"] is True


def test_sticky_alias_failure_cooldown_ignores_request_errors(monkeypatch):
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.1-FP8=GLM-5.1-FP8@llm,GLM-5.1-FP8@llm3",
    )
    monkeypatch.setenv("LITELLM_STICKY_ROUTING_FAILURE_THRESHOLD", "1")
    logger = TrajectoryLogger()

    kwargs = {"metadata": {"sticky_routed_model": "GLM-5.1-FP8@llm"}}
    logger._mark_sticky_alias_failure(kwargs, {"status_code": 400})

    assert not logger._is_sticky_alias_on_cooldown("GLM-5.1-FP8@llm")


def test_sticky_alias_failure_cooldown_tracks_transient_errors(monkeypatch):
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.1-FP8=GLM-5.1-FP8@llm,GLM-5.1-FP8@llm3",
    )
    monkeypatch.setenv("LITELLM_STICKY_ROUTING_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("LITELLM_STICKY_ROUTING_COOLDOWN_SECONDS", "60")
    logger = TrajectoryLogger()

    kwargs = {"metadata": {"sticky_routed_model": "GLM-5.1-FP8@llm"}}
    logger._mark_sticky_alias_failure(kwargs, {"status_code": 500})

    assert logger._is_sticky_alias_on_cooldown("GLM-5.1-FP8@llm")


def test_sticky_alias_failure_cooldown_infers_alias_from_api_base(monkeypatch):
    monkeypatch.setenv(
        _STICKY_ROUTING_ALIASES_ENV,
        "GLM-5.1-FP8=GLM-5.1-FP8@llm,GLM-5.1-FP8@llm35",
    )
    monkeypatch.setenv("LITELLM_STICKY_ROUTING_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("LITELLM_STICKY_ROUTING_COOLDOWN_SECONDS", "60")
    logger = TrajectoryLogger()

    kwargs = {"model": "GLM-5.1-FP8"}
    logger._mark_sticky_alias_failure(
        kwargs,
        {
            "status_code": 530,
            "deployment": {"api_base": "https://llm35.example.com/v1"},
        },
    )

    assert logger._is_sticky_alias_on_cooldown("GLM-5.1-FP8@llm35")


def test_redact_sensitive_preserves_token_usage_metrics():
    logger = TrajectoryLogger()

    redacted = logger._redact_sensitive(
        {
            "api_key": "sk-test",
            "authorization": "Bearer secret",
            "auth_token": "secret-token",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["auth_token"] == "[REDACTED]"
    assert redacted["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def _build_logged_response_message(logger: TrajectoryLogger, message: dict) -> dict:
    response_body = logger._build_openai_response_body(
        {},
        {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": message,
                }
            ]
        },
        usage={},
    )
    return response_body["choices"][0]["message"]


def test_response_body_preserves_original_thinking_blocks():
    logger = TrajectoryLogger()
    thinking_blocks = [
        {
            "type": "thinking",
            "thinking": "Inspect the repository first.",
            "signature": "signed-thinking",
        },
        {
            "type": "thinking",
            "thinking": "",
            "signature": "signed-omitted-thinking",
        },
        {
            "type": "redacted_thinking",
            "data": "encrypted-redacted-thinking",
        },
    ]

    logged_message = _build_logged_response_message(
        logger,
        {
            "role": "assistant",
            "content": None,
            "thinking_blocks": thinking_blocks,
        },
    )

    assert logged_message["thinking_blocks"] == thinking_blocks
    assert logged_message["reasoning_content"] == "Inspect the repository first."


def test_response_body_reads_provider_specific_thinking_blocks():
    logger = TrajectoryLogger()
    thinking_blocks = [
        {
            "type": "thinking",
            "thinking": "Use the compatibility field.",
            "signature": "provider-signature",
        }
    ]

    logged_message = _build_logged_response_message(
        logger,
        {
            "role": "assistant",
            "content": None,
            "provider_specific_fields": {"thinking_blocks": thinking_blocks},
        },
    )

    assert logged_message["thinking_blocks"] == thinking_blocks


def test_response_body_omits_thinking_blocks_for_other_models():
    logger = TrajectoryLogger()

    logged_message = _build_logged_response_message(
        logger,
        {
            "role": "assistant",
            "content": "A normal response.",
            "provider_specific_fields": {"thinking_blocks": None},
        },
    )

    assert logged_message == {
        "role": "assistant",
        "content": "A normal response.",
    }


def test_response_summary_reports_thinking_blocks():
    logger = TrajectoryLogger()

    content_types = logger._summarize_response_types(
        {
            "choices": [
                {
                    "message": {
                        "thinking_blocks": [
                            {
                                "type": "thinking",
                                "thinking": "",
                                "signature": "signature",
                            }
                        ]
                    }
                }
            ]
        }
    )

    assert content_types == ["thinking"]
