import asyncio
import subprocess
from unittest.mock import AsyncMock

import httpx
import pytest
from config import ConfigurationError, github_settings, settings
from langchain_core.messages import AIMessage, ToolMessage
from llm_provider import create_llm
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
    answers = iter([
        "configure llm and services", "ollama", "", "", "jira",
        "https://jira.example.test", "dev@example.test", "",
    ])
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
        "LLM_OPENAI_COMPATIBLE_MODEL": "qwen3:8b",
        "LLM_OPENAI_COMPATIBLE_BASE_URL": setup_env.OLLAMA_URL,
        "LLM_OPENAI_COMPATIBLE_API_KEY": "ollama",
        "JIRA_BASE_URL": "https://jira.example.test",
        "JIRA_EMAIL": "dev@example.test",
        "JIRA_API_TOKEN": "fixture-token",
        "JIRA_JQL": 'project = "CP" AND status = "To Do"',
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


def test_existing_config_can_add_all_services_without_replacing_model(monkeypatch, private_env, capsys):
    private_env.write_text("# Keep this comment\nLLM_PROVIDER=openai\n", encoding="utf-8")
    answers = iter([
        "configure services only", "jira, confluence, and github",
        "http://jira.example.test", "https://jira.example.test", "wrong-email", "jira@example.test",
        "https://wiki.example.test/wiki/spaces", "https://wiki.example.test/wiki", "wiki@example.test",
        "https://example.test/test-owner/test-repo",
        "https://github.com/test-owner/test-repo.git", "develop",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda _prompt: "fixture-token")
    setup_env.configure()
    values = settings(private_env, {})
    assert values["LLM_PROVIDER"] == "openai"
    assert values["JIRA_EMAIL"] == "jira@example.test"
    assert values["CONFLUENCE_EMAIL"] == "wiki@example.test"
    assert values["GITHUB_REPO"] == "test-owner/test-repo"
    assert values["GITHUB_BRANCH"] == "develop"
    assert values["GITHUB_TOKEN"] == "fixture-token"
    assert "# Keep this comment" in private_env.read_text(encoding="utf-8-sig")
    output = capsys.readouterr().out
    assert "Jira site URL:" in output and "Example: https://capstoneprojectgrp4.atlassian.net/." in output
    assert "Jira account email:" in output and "Example: PES2PGE25DS065@stu.pes.edu." in output
    assert "Confluence site URL:" in output and "Example: https://capstoneprojectgrp4.atlassian.net/wiki/." in output
    assert "GitHub repository:" in output and "https://github.com/pavi550/Capstone.git" in output
    assert "fixture-token" not in output


def test_setup_builds_openai_to_ollama_fallback(monkeypatch):
    answers = iter(["", "ollama", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda _prompt: "fixture-key")
    values = setup_env.model_chain("openai")
    llm = create_llm(values=values)
    assert [candidate[0] for candidate in llm._candidates] == ["openai", "openai_compatible"]
    assert values["LLM_OPENAI_COMPATIBLE_MODEL"] == "qwen3:8b"
    assert str(llm._candidates[1][1].openai_api_base).rstrip("/") == setup_env.OLLAMA_URL


def test_read_only_confluence_check(monkeypatch, capsys):
    import confluence_check

    def send(request):
        assert request.method == "GET"
        assert request.url == httpx.URL("https://wiki.example.test/wiki/api/v2/spaces?limit=1")
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(send))
    monkeypatch.setattr(confluence_check.httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setattr(confluence_check, "settings", lambda: {
        "CONFLUENCE_BASE_URL": "https://wiki.example.test",
        "CONFLUENCE_EMAIL": "dev@example.test",
        "CONFLUENCE_API_TOKEN": "fixture-token",
    })
    confluence_check.check()
    assert "read-only API check passed" in capsys.readouterr().out


@pytest.mark.parametrize("repository", [
    "pavi550/Capstone", "https://github.com/pavi550/Capstone/",
    "https://github.com/pavi550/Capstone.git",
])
def test_github_settings_accepts_repository_urls(repository):
    assert github_settings({"GITHUB_REPO": repository, "GITHUB_TOKEN": "fixture"}) == (
        "fixture", "pavi550", "Capstone", "main",
    )


@pytest.mark.parametrize("repository", [
    "https://github.com.example.test/owner/repo",
    "https://github.com/owner/repo/tree/main",
    "https://secret@github.com/owner/repo",
    "https://github.com/owner/repo?token=secret",
])
def test_github_settings_rejects_non_repository_urls(repository):
    with pytest.raises(ConfigurationError, match="HTTPS github.com repository URL") as error:
        github_settings({"GITHUB_REPO": repository, "GITHUB_TOKEN": "fixture"})
    assert "secret" not in str(error.value)


def test_smoke_test_failure_names_failed_stage(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["setup_env.py", "--smoke-only"])
    monkeypatch.setattr(setup_env, "install", lambda **_: "fixture-python")

    def fail(*_args):
        raise subprocess.CalledProcessError(1, ["pytest"])

    monkeypatch.setattr(setup_env, "run_python", fail)
    assert setup_env.main() == 1
    error = capsys.readouterr().err
    assert "Setup failed at offline smoke test." in error
    assert "uses no service credentials" in error


def test_install_reuses_environment_when_inputs_and_dependencies_match(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(setup_env, "ROOT", tmp_path)
    monkeypatch.setattr(setup_env, "install_fingerprint", lambda: "fixture-fingerprint")
    venv = tmp_path / ".venv"
    python = venv / ("Scripts/python.exe" if setup_env.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (venv / ".capstone-dependencies").write_bytes(b"fixture-fingerprint")
    calls = []

    def record(_python, *args, **_kwargs):
        calls.append(args)

    monkeypatch.setattr(setup_env, "run_python", record)
    assert setup_env.install() == python
    assert len(calls) == 2
    assert calls[1] == ("-m", "pip", "check")
    assert "Dependency installation skipped" in capsys.readouterr().out


def test_precheck_rejects_changed_or_broken_dependencies(monkeypatch, tmp_path):
    stamp = tmp_path / "stamp"
    stamp.write_bytes(b"old-fingerprint")

    def broken(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["pip", "check"])

    monkeypatch.setattr(setup_env, "run_python", broken)
    assert not setup_env.dependencies_ready("fixture-python", stamp, "new-fingerprint")
    assert not setup_env.dependencies_ready("fixture-python", stamp, "old-fingerprint")
