---
name: capstone-mcp-multi-llm
description: Set up and troubleshoot Capstone Jira/GitHub MCP orchestrators with Python 3.12, Gemini, Groq, Ollama, OpenRouter, optional OpenAI, and tool-aware fallback.
---
# Capstone MCP Multi-LLM Skill (updated)

## Goal
Maintain the existing Capstone Jira and GitHub MCP orchestrators, using a single tool-bound LLM interface and configurable multi-provider fallback. Do not replace application-specific orchestrator logic.

## Project paths
- Root: `Capstone1/`
- MCP app: `Capstone1/Capstone/mcp/`
- Virtual environment: `Capstone1/.venv312/`
- Skill package: `Capstone1/capstone-multi-llm-skill/`
Paths in scripts are resolved dynamically. Set `CAPSTONE_ROOT` if detection fails.

## Setup
1. Inspect the existing `run.sh`, `llm_provider.py`, `ai_orchestrator.py`, `github_orchestrator.py`, and requirements. Back up files before replacing them.
2. Run `bash capstone-multi-llm-skill/setup_env.sh` from `Capstone1/`. It creates/reuses Python 3.12 venv, installs dependencies, and validates imports.
3. If `Capstone/mcp/.env` does not exist, setup creates it from `.env.example`. Edit it locally, never paste secrets into chat or commit `.env` to Git.
4. Replace `Capstone/mcp/llm_provider.py` **only after reviewing compatibility** with `llm_provider_example.py`. Its public function is `get_llm_with_tools(llm_tools)`, returning a Runnable with async `.ainvoke(messages)`.
5. Existing `run.sh` must load `Capstone/mcp/.env` and use `$VIRTUAL_ENV/bin/python` (or `../../.venv312/bin/python`) rather than a nonexistent `mcp/.venv/bin/python`.
6. Test individual providers and `auto` without Jira/GitHub writes. Run `python capstone-multi-llm-skill/health_check.py` from the project root to smoke-test model connectivity; this does **not** validate MCP tool execution.
7. Finally run `source run.sh jira auto` or `source run.sh github auto` from `Capstone/mcp/` after verifying tools and permissions.

## Providers and models
- Gemini: `GOOGLE_API_KEY`, `GEMINI_MODEL` (example `gemini-2.5-flash`).
- Groq: `GROQ_API_KEY`, `GROQ_MODEL` (example `llama-3.3-70b-versatile`).
- Ollama: `OLLAMA_MODEL`, `OLLAMA_BASE_URL`; Ollama server must be running and model pulled (`ollama pull qwen2.5:7b`).
- OpenRouter: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`; explicitly select a tool-calling-capable model.
- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL`; requires available API billing credits.

Model availability, free quotas, and tool-call support change. Verify them with the provider. Tool binding must succeed for each candidate. `auto` skips missing configuration, not providers that are configured but unreachable until invocation fails.

## Fallback behavior
- `LLM_PROVIDER=gemini|groq|ollama|openrouter|openai|auto`.
- `LLM_FALLBACK_ORDER` controls auto mode; defaults to `gemini,groq,ollama,openrouter,openai`.
- `LLM_MAX_ATTEMPTS` controls bounded retry attempts (1–5) per provider for transient 408/429/5xx/timeouts; billing/quota exhaustion is not retried.
- Failed invocations move to the next provider; errors are summarized after all providers fail.
- **Never blindly retry Jira/GitHub write tools.** Only model invocation is retried. Review tool-call schemas across model providers and maintain conversation/tool-call IDs correctly.

## Troubleshooting
- `ModuleNotFoundError`: inspect interpreter printed by `run.sh`, and install with that Python's `-m pip`.
- Python 3.9: switch to `.venv312` using Python 3.12.
- Gemini 503/504: temporary server failure; retry then fail over.
- OpenAI 429 `no credits remaining`: fix API billing or skip OpenAI.
- Ollama connection failure: start Ollama and check `ollama list`.
- Unknown model/tool-binding error: set valid, tool-capable model IDs.
- Health-check success only confirms basic inference, not reliable MCP tool use.

## Files
- `setup_env.sh`: environment setup and dependency verification.
- `requirements-multi-llm.txt`: Python dependencies.
- `.env.example`: editable, non-secret configuration.
- `llm_provider_example.py`: updated provider router; adapt before copying into app.
- `health_check.py`: optional read-only inference smoke test.
