# LiteLLM and vLLM Helpers

This directory contains the local model proxy, backend launchers, and request
trajectory callback used by this fork.

## Files

- `serve_litellm.sh`: validates configuration and starts the LiteLLM proxy.
- `trajectory_logger.py`: records trajectories, assigns sticky backend aliases,
  and implements failure cooldown/fallback.
- `litellm_config.example.yaml`: placeholder OpenAI-compatible configuration.
- `litellm_config_anthropic.example.yaml`: placeholder Anthropic-compatible
  configuration.
- `serve_vllm_*.sh`: model-specific local vLLM launchers.
- `serve_vllm_auto_resume.sh`: restarts a selected vLLM launcher after GPU
  memory is released.
- `litellm_log/`: local proxy logs.
- `trajectories/`: fallback output when a request does not provide
  `x-trajectory-output-path`.

## Quick Start

```bash
conda create -n harbor-litellm python=3.13
conda activate harbor-litellm
pip install "litellm[proxy]==1.83.9"
cp scripts/serve_llm/litellm_config.example.yaml \
  scripts/serve_llm/litellm_config.yaml
bash scripts/serve_llm/serve_litellm.sh
```

Tracked example configurations contain placeholders. Local configurations can
contain private endpoints or credentials and must remain untracked.

See [LLM services and tracing](../../docs_dev/api-services.md) and
[custom agents](../../docs_dev/custom-agents.md) for configuration, routing,
trajectory, and runtime details.
