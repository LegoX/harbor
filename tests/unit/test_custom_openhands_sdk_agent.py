"""Unit tests for the custom OpenHands SDK Harbor agent."""

import json
import runpy
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from harbor.agents.custom.openhands_sdk import CustomOpenHandsSDK
from harbor.agents.custom.openhands_sdk_runner import (
    parse_send_reasoning_content_models,
)
from harbor.models.agent.context import AgentContext


def test_parse_send_reasoning_content_models_csv():
    """CSV input should be parsed into a deduplicated list."""
    assert parse_send_reasoning_content_models("glm-5, qwen3, glm-5") == [
        "glm-5",
        "qwen3",
    ]


def test_parse_send_reasoning_content_models_json():
    """JSON array input should also be accepted."""
    assert parse_send_reasoning_content_models('["glm-5", "qwen3"]') == [
        "glm-5",
        "qwen3",
    ]


@pytest.mark.parametrize("runtime_version", ["1.14.0", "1.33.0"])
def test_versioned_runtime_builds_reasoning_effort_kwargs(
    runtime_version: str,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "agent_runtimes"
        / "openhands-sdk"
        / runtime_version
        / "runtime"
        / "run_openhands_harbor.py"
    )
    build_llm_kwargs = runpy.run_path(str(runtime_path))["_build_llm_kwargs"]

    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    assert build_llm_kwargs("test/model", "test-key", "https://llm.test") == {
        "model": "test/model",
        "api_key": "test-key",
        "base_url": "https://llm.test",
        "reasoning_effort": None,
    }

    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    assert build_llm_kwargs(
        "test/model",
        "test-key",
        "https://llm.test",
        usage_id="condenser",
    ) == {
        "model": "test/model",
        "api_key": "test-key",
        "base_url": "https://llm.test",
        "reasoning_effort": "low",
        "usage_id": "condenser",
    }


class TestCustomOpenHandsSDKAgent:
    """Tests for the custom OpenHands SDK adapter."""

    def test_default_reasoning_effort_is_unset(self, tmp_path: Path):
        agent = CustomOpenHandsSDK(logs_dir=tmp_path, model_name="test/model")

        assert agent._reasoning_effort is None

    def test_default_version_uses_1_33_runtime(self, tmp_path: Path):
        agent = CustomOpenHandsSDK(logs_dir=tmp_path, model_name="test/model")

        assert agent.version() == "1.33.0"
        assert agent._runtime_recipe_version() == "1.33.0"

    def test_explicit_version_selects_matching_runtime(self, tmp_path: Path):
        agent = CustomOpenHandsSDK(
            logs_dir=tmp_path,
            model_name="test/model",
            version="1.14.0",
        )

        assert agent.version() == "1.14.0"
        assert agent._runtime_recipe_version() == "1.14.0"

    def test_init_send_reasoning_content_models(self):
        """The constructor should normalize the configured model list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = CustomOpenHandsSDK(
                logs_dir=Path(tmpdir),
                model_name="test/model",
                send_reasoning_content_models="glm-5, qwen3, glm-5",
            )

            assert agent._send_reasoning_content_models == ["glm-5", "qwen3"]

    @patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_run_with_send_reasoning_content_models(self):
        """run() should forward configured model prefixes to the runner."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = CustomOpenHandsSDK(
                logs_dir=Path(tmpdir),
                model_name="test/model",
                send_reasoning_content_models="glm-5, qwen3",
            )
            mock_env = AsyncMock()
            mock_env.exec.return_value = AsyncMock(return_code=0, stdout="", stderr="")

            await agent.run("Test instruction", mock_env, AsyncMock())

            env = mock_env.exec.call_args_list[0].kwargs["env"]
            assert json.loads(env["SEND_REASONING_CONTENT_MODELS"]) == [
                "glm-5",
                "qwen3",
            ]
            trajectory_output_path = str(
                (Path(tmpdir) / "litellm-trajectory.jsonl").resolve()
            )
            assert json.loads(env["LITELLM_EXTRA_HEADERS"]) == {
                "x-trajectory-output-path": trajectory_output_path,
                "x-litellm-routing-key": Path(tmpdir).name,
            }
            assert json.loads(env["LITELLM_EXTRA_BODY"]) == {
                "metadata": {
                    "trajectory_output_path": trajectory_output_path,
                    "sticky_routing_key": Path(tmpdir).name,
                    "instance_id": Path(tmpdir).name,
                }
            }
            assert "LLM_REASONING_EFFORT" not in env

    @patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_run_forwards_explicit_reasoning_effort(self, tmp_path: Path):
        agent = CustomOpenHandsSDK(
            logs_dir=tmp_path,
            model_name="test/model",
            reasoning_effort="low",
        )
        mock_env = AsyncMock()
        mock_env.exec.return_value = AsyncMock(return_code=0, stdout="", stderr="")

        await agent.run("Test instruction", mock_env, AsyncMock())

        env = mock_env.exec.call_args_list[0].kwargs["env"]
        assert env["LLM_REASONING_EFFORT"] == "low"

    @patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_run_merges_routing_metadata_with_token_id_collection(
        self, tmp_path: Path
    ):
        """run() should preserve token ID collection while adding routing metadata."""
        logs_dir = tmp_path / "jobs" / "job" / "owner__repo-123__abc1234" / "agent"
        agent = CustomOpenHandsSDK(
            logs_dir=logs_dir,
            model_name="test/model",
            collect_token_ids=True,
        )
        mock_env = AsyncMock()
        mock_env.exec.return_value = AsyncMock(return_code=0, stdout="", stderr="")

        await agent.run("Test instruction", mock_env, AsyncMock())

        env = mock_env.exec.call_args_list[0].kwargs["env"]
        trajectory_output_path = "job/owner__repo-123__abc1234"
        assert json.loads(env["LITELLM_EXTRA_HEADERS"]) == {
            "x-trajectory-output-path": trajectory_output_path,
            "x-litellm-routing-key": "owner__repo-123",
        }
        assert json.loads(env["LITELLM_EXTRA_BODY"]) == {
            "return_token_ids": True,
            "metadata": {
                "trajectory_output_path": trajectory_output_path,
                "sticky_routing_key": "owner__repo-123",
                "instance_id": "owner__repo-123",
            },
        }

    def test_populate_context_reads_trajectory_metrics(self, tmp_path: Path):
        """populate_context_post_run() should load metrics from trajectory.json."""
        agent = CustomOpenHandsSDK(logs_dir=tmp_path, model_name="test/model")
        trajectory = {
            "final_metrics": {
                "total_prompt_tokens": 1000,
                "total_completion_tokens": 500,
                "total_cached_tokens": 200,
                "total_cost_usd": 0.05,
            }
        }
        (tmp_path / "trajectory.json").write_text(json.dumps(trajectory))

        context = AgentContext()
        agent.populate_context_post_run(context)

        assert context.cost_usd == 0.05
        assert context.n_input_tokens == 1000
        assert context.n_output_tokens == 500
        assert context.n_cache_tokens == 200

    def test_populate_context_logs_missing_trajectory_at_debug(self, tmp_path: Path):
        """A missing trajectory is diagnostic and should only debug log."""
        logger = Mock()
        child_logger = Mock()
        logger.getChild.return_value = child_logger
        agent = CustomOpenHandsSDK(
            logs_dir=tmp_path,
            model_name="test/model",
            logger=logger,
        )

        agent.populate_context_post_run(AgentContext())

        child_logger.debug.assert_called_once()
        child_logger.warning.assert_not_called()
        child_logger.error.assert_not_called()

    def test_populate_context_logs_parse_failure_at_debug(self, tmp_path: Path):
        """A malformed trajectory is diagnostic and should only debug log."""
        logger = Mock()
        child_logger = Mock()
        logger.getChild.return_value = child_logger
        agent = CustomOpenHandsSDK(
            logs_dir=tmp_path,
            model_name="test/model",
            logger=logger,
        )
        (tmp_path / "trajectory.json").write_text("{")

        agent.populate_context_post_run(AgentContext())

        child_logger.debug.assert_called_once()
        child_logger.warning.assert_not_called()
        child_logger.error.assert_not_called()
