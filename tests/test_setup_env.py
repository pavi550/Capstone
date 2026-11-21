import asyncio
from unittest.mock import AsyncMock

import pytest
from config import settings
from langchain_core.messages import AIMessage, ToolMessage
from setup_health import TOOL_NAME, check_provider

import setup_env


@pytest.fixture
def private_env(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    monkeypatch.setattr(setup_env, "ENV_PATH", path)
    return path


def test_offline_choice_leaves_no_env(monkeypatch, private_env):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    setup_env.configure()
    assert not private_env.exists()


def test_configure_ollama_jira_without_sending_requests(monkeypatch, private_env):
    answers = iter(["ollama", "", "jira", "https://jira.example.test", "dev@example.test", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda _prompt: "fixture-token")

    def unexpected(_values):
        pytest.fail("Declining the optional check must avoid provider calls")

    monkeypatch.setattr(setup_env, "check_llm", unexpected)
    setup_env.configure()
    assert settings(private_env, {}) == {
        "LLM_PROVIDER": "openai_compatible",
        "LLM_MODEL": "qwen3:8b",
        "LLM_TIMEOUT_SECONDS": "180",
        "LLM_FALLBACK_PROVIDERS": "",
        "LLM_BASE_URL": setup_env.OLLAMA_URL,
        "LLM_API_KEY": "ollama",
        "JIRA_BASE_URL": "https://jira.example.test",
        "JIRA_EMAIL": "dev@example.test",
        "JIRA_API_TOKEN": "fixture-token",
    }


def test_dotenv_values_round_trip_and_existing_env_is_preserved(monkeypatch, private_env):
    test_key = "fixture 'quoted' \\ value # $"
    setup_env.write_env({"OPENAI_API_KEY": test_key})
    original = private_env.read_bytes()
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    setup_env.configure()
    assert private_env.read_bytes() == original
    assert settings(private_env, {}) == {"OPENAI_API_KEY": test_key}


def test_optional_check_returns_mock_tool_result(monkeypatch, capsys):
    response = AIMessage(content="", tool_calls=[{"name": TOOL_NAME, "id": "mock", "args": {}}])
    model = AsyncMock()
    model.ainvoke.side_effect = [response, AIMessage(content="DEMO-947 is In Progress.")]
    monkeypatch.setattr("setup_health.get_llm_with_tools", lambda *_args, **_kwargs: model)
    asyncio.run(check_provider({"LLM_PROVIDER": "openai"}))
    tool_result = model.ainvoke.await_args.args[0][-1]
    assert isinstance(tool_result, ToolMessage)
    assert tool_result.tool_call_id == "mock"
    assert "LLM test passed" in capsys.readouterr().out
