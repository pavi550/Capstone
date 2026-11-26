import asyncio
import importlib
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import config
import httpx
import httpx2
import launch
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from llm_provider import LLMError, ToolLLM, create_llm
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "Find an issue",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}},
    },
}
CALL = {"name": "lookup", "id": "call_123", "args": {"key": "CP-1"}}
KEY = "synthetic-credential-do-not-display"
JIRA = {
    "JIRA_BASE_URL": "https://jira.example.test",
    "JIRA_EMAIL": "developer@example.test",
    "JIRA_API_TOKEN": KEY,
}
GITHUB = {"GITHUB_TOKEN": KEY, "GITHUB_REPO": "example/repo"}
GITHUB_TOOLS = {
    "list_repo_files", "search_repo_code", "read_file", "create_branch",
    "commit_file_change", "create_pull_request",
}
JIRA_TOOLS = {
    "list_open_jiras", "create_jira_ticket", "update_jira_fields", "add_jira_comment",
    "update_jira_status",
}


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("MCP tests require a mocked HTTP transport")

    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected)
    monkeypatch.setattr(httpx2.AsyncClient, "send", unexpected)
    monkeypatch.setattr("requests.sessions.Session.send", unexpected)


def test_dotenv_precedence_and_absolute_location(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = repo / ".env"
    env_file.write_text('LLM_MODEL="developer model"\nGOOGLE_API_KEY=file-key\n', encoding="utf-8")
    monkeypatch.delenv("CAPSTONE_ENV_FILE")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "process-key")
    values = config.settings()
    assert values["LLM_MODEL"] == "developer model"
    assert values["GOOGLE_API_KEY"] == "process-key"
    assert config.settings(environ={"GOOGLE_API_KEY": ""})["GOOGLE_API_KEY"] == ""


def test_missing_dotenv_uses_process_variables(tmp_path):
    assert config.settings(tmp_path / ".env", {"LLM_PROVIDER": "openai"}) == {
        "LLM_PROVIDER": "openai",
    }


def test_four_developer_configs(tmp_path):
    for developer in range(4):
        clone = tmp_path / str(developer)
        clone.mkdir()
        env_file = clone / ".env"
        env_file.write_text(f"OPENAI_API_KEY=local-{developer}\n", encoding="utf-8")
        assert config.settings(env_file, {})["OPENAI_API_KEY"] == f"local-{developer}"


@pytest.mark.parametrize("values,missing", [
    ({"LLM_PROVIDER": "gemini"}, "GOOGLE_API_KEY"),
    ({"LLM_PROVIDER": "auto", "GOOGLE_API_KEY": KEY}, "OPENAI_API_KEY"),
    ({"LLM_PROVIDER": "anthropic", "LLM_MODEL": "custom"}, "ANTHROPIC_API_KEY"),
])
def test_missing_active_key(values, missing):
    with pytest.raises(config.ConfigurationError, match=missing) as error:
        create_llm([TOOL], values=values)
    assert KEY not in str(error.value)


def test_service_settings_validation_is_independent():
    assert config.github_settings(GITHUB) == (KEY, "example", "repo", "main")
    assert config.jira_settings(JIRA) == ("https://jira.example.test", "developer@example.test", KEY)
    with pytest.raises(config.ConfigurationError, match="JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN"):
        config.jira_settings(GITHUB)


@pytest.mark.parametrize("provider,key,expected_class,url", [
    ("openai", "OPENAI_API_KEY", "ChatOpenAI", "https://api.openai.com/v1"),
    ("gemini", "GOOGLE_API_KEY", "ChatGoogleGenerativeAI", "https://generativelanguage.googleapis.com"),
    ("anthropic", "ANTHROPIC_API_KEY", "SecureChatAnthropic", "https://api.anthropic.com"),
    ("openai_compatible", "LLM_API_KEY", "ChatOpenAI", "http://localhost:11434/v1"),
])
def test_factory_binds_real_adapters(provider, key, expected_class, url):
    model = "custom-tool-model"
    vals = {
        key: KEY, "LLM_PROVIDER": provider, "LLM_MODEL": "generic-model",
        f"LLM_{provider.upper()}_MODEL": "provider-model", "LLM_TIMEOUT_SECONDS": "3",
        "LLM_BASE_URL": "http://localhost:11434/v1",
    }
    choices = create_llm([TOOL], model=model, values=vals)._candidates
    (_, binding, timeout), = choices
    assert type(binding.bound).__name__ == expected_class
    assert getattr(binding.bound, "model", getattr(binding.bound, "model_name", "")) == model
    bound_url = (getattr(binding.bound, "base_url", None)
                 or getattr(binding.bound, "anthropic_api_url", None)
                 or binding.bound.openai_api_base)
    assert bound_url == url
    assert timeout == 3
    assert "tools" in binding.kwargs


def test_native_endpoint_requires_paired_key(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ambient.example.test")
    default = create_llm([TOOL], values={"OPENAI_API_KEY": KEY}, provider="openai")
    assert default._candidates[0][1].bound.openai_api_base == "https://api.openai.com/v1"
    custom = {"OPENAI_API_KEY": KEY, "LLM_OPENAI_BASE_URL": "https://custom.example.test/v1"}
    with pytest.raises(config.ConfigurationError, match="LLM_OPENAI_API_KEY"):
        create_llm([TOOL], values=custom, provider="openai")
    custom["LLM_OPENAI_API_KEY"] = "synthetic-custom-key"
    adapter = create_llm([TOOL], model="custom", values=custom, provider="openai")
    assert adapter._candidates[0][1].bound.openai_api_base == custom["LLM_OPENAI_BASE_URL"]


@pytest.mark.parametrize("url", [
    "http://remote.example.test/v1", f"https://user:{KEY}@example.test/v1",
    f"https://example.test/v1?key={KEY}", "file:///tmp/service",
])
def test_unsafe_endpoint_rejected(url):
    with pytest.raises(config.ConfigurationError, match="LLM_BASE_URL") as error:
        create_llm([TOOL], provider="openai_compatible", model="custom",
                   values={"LLM_BASE_URL": url, "LLM_API_KEY": KEY})
    assert url not in str(error.value)
    assert KEY not in str(error.value)


def test_provider_options_and_model_precedence():
    adapter = create_llm([TOOL], provider="anthropic", values={
        "ANTHROPIC_API_KEY": KEY, "LLM_MODEL": "global", "LLM_ANTHROPIC_MODEL": "specific",
        "LLM_TEMPERATURE": "2", "LLM_ANTHROPIC_TEMPERATURE": "0.3",
    })._candidates[0][1].bound
    assert adapter.model == "specific"
    assert adapter.temperature == 0.3
    with pytest.raises(config.ConfigurationError, match="LLM_TEMPERATURE"):
        create_llm(provider="anthropic", model="custom",
                   values={"ANTHROPIC_API_KEY": KEY, "LLM_TEMPERATURE": "2"})


def test_invalid_provider_hides_input():
    with pytest.raises(config.ConfigurationError, match="LLM_PROVIDER") as error:
        create_llm(provider=KEY, values={})
    assert KEY not in str(error.value)


def test_unsupported_model_profile(monkeypatch):
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **_: SimpleNamespace(
        profile={"tool_calling": False},
    ))
    with pytest.raises(LLMError, match="Select a tool-capable model"):
        create_llm([TOOL], provider="openai", model="custom", values={"OPENAI_API_KEY": KEY})


def test_configured_chain_and_default_chain():
    keys = {"OPENAI_API_KEY": KEY, "GOOGLE_API_KEY": KEY, "ANTHROPIC_API_KEY": KEY}
    default = create_llm([TOOL], provider="auto", values=keys)
    assert [entry[0] for entry in default._candidates] == ["gemini", "openai"]
    custom = create_llm([TOOL], provider="auto", values=dict(
        keys, LLM_FALLBACK_PROVIDERS="gemini,openai,anthropic",
        LLM_MODEL="primary-only", LLM_ANTHROPIC_MODEL="fallback-only",
    ))
    assert [entry[0] for entry in custom._candidates] == ["gemini", "openai", "anthropic"]
    assert custom._candidates[0][1].bound.model == "primary-only"
    assert custom._candidates[2][1].bound.model == "fallback-only"


class ProviderFailure(Exception):
    def __init__(self, status_code, message=KEY):
        super().__init__(message)
        self.status_code = status_code


def adapters(*results):
    mocks = [
        AsyncMock(side_effect=result) if isinstance(result, Exception)
        else AsyncMock(return_value=result)
        for result in results
    ]
    names = ("gemini", "openai", "anthropic")
    return ToolLLM([
        (name, SimpleNamespace(ainvoke=mock), 2)
        for name, mock in zip(names, mocks, strict=False)
    ]), mocks


@pytest.mark.parametrize("error", [TimeoutError(), ProviderFailure(429)])
def test_safe_fallback(error):
    response = AIMessage(content="done")
    llm, (primary, fallback) = adapters(error, response)
    history = [HumanMessage(content="request")]
    assert asyncio.run(llm.ainvoke(history)) == response
    primary.assert_awaited_once_with(history)
    fallback.assert_awaited_once_with(history)


def test_primary_success():
    response = AIMessage(content="done")
    llm, (primary, fallback) = adapters(response, AIMessage(content="unused"))
    assert asyncio.run(llm.ainvoke([])) == response
    primary.assert_awaited_once()
    fallback.assert_not_awaited()


def test_all_providers_unavailable():
    llm, mocks = adapters(ProviderFailure(503), ProviderFailure(429), TimeoutError(KEY))
    with pytest.raises(LLMError, match="All eligible") as error:
        asyncio.run(llm.ainvoke([]))
    assert KEY not in str(error.value)
    assert [mock.await_count for mock in mocks] == [1, 1, 1]


@pytest.mark.parametrize("error,message", [
    (ProviderFailure(401), "authentication"),
    (ProviderFailure(400, "tools unsupported"), "tool-capable model"),
    (ProviderFailure(400, "invalid schema"), "request failed"),
    (TypeError(KEY), "request failed"),
])
def test_non_retryable_error(error, message):
    llm, (_, fallback) = adapters(error, AIMessage(content="unused"))
    with pytest.raises(LLMError, match=message) as caught:
        asyncio.run(llm.ainvoke([]))
    assert KEY not in str(caught.value)
    fallback.assert_not_awaited()


def test_fallback_preserves_existing_tool_results():
    first = AIMessage(content="Looking up", tool_calls=[CALL], additional_kwargs={"provider": "data"})
    final = AIMessage(content="done")
    llm, (primary, fallback) = adapters(first, final)
    primary.side_effect = [first, ProviderFailure(429)]
    history = [HumanMessage(content="request")]

    async def workflow():
        history.append(await llm.ainvoke(history))
        history.append(ToolMessage(content="result", tool_call_id=CALL["id"]))
        assert await llm.ainvoke(history) == final

    asyncio.run(workflow())
    replay = fallback.await_args.args[0]
    assert replay[0] is history[0]
    assert replay[1].text == first.text
    assert replay[1].tool_calls == first.tool_calls
    assert replay[1].additional_kwargs == {}
    assert replay[2] is history[2]
    assert history[1].additional_kwargs == {"provider": "data"}


@pytest.mark.parametrize("provider,model,host,payload,key_header", [
    ("openai", "gpt-4o-mini", "api.openai.com", "openai", "authorization"),
    ("openai_compatible", "local-tool-model", "127.0.0.1", "openai", "authorization"),
    ("gemini", "gemini-3.8-flash", "generativelanguage.googleapis.com", "gemini", "x-goog-api-key"),
    ("anthropic", "claude-sonnet-4-6", "api.anthropic.com", "anthropic", "authorization"),
])
def test_mocked_native_and_compatible_wire_calls(monkeypatch, provider, model, host, payload, key_header):
    bodies = {
        "openai": {
            "id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": CALL["id"], "type": "function",
                    "function": {"name": CALL["name"], "arguments": json.dumps(CALL["args"])},
                }],
            }}],
        },
        "gemini": {"candidates": [{
            "content": {"role": "model", "parts": [
                {"functionCall": {"name": CALL["name"], "args": CALL["args"]}},
            ]}, "finishReason": "STOP",
        }]},
        "anthropic": {
            "id": "msg_test", "model": model, "role": "assistant", "type": "message",
            "content": [{"type": "tool_use", "id": CALL["id"], "name": CALL["name"],
                         "input": CALL["args"]}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 30, "output_tokens": 5},
        },
    }
    seen = []

    async def send(client, request, **kwargs):
        seen.append(request)
        assert not client.follow_redirects
        assert kwargs.get("follow_redirects") is not True
        assert request.url.host == host
        assert request.headers[key_header].split()[-1] == KEY
        assert "lookup" in json.dumps(json.loads(request.content)["tools"])
        response_type = httpx2.Response if payload == "anthropic" else httpx.Response
        return response_type(200, json=bodies[payload], request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(httpx2.AsyncClient, "send", send)
    values = {
        "OPENAI_API_KEY": KEY, "GOOGLE_API_KEY": KEY, "ANTHROPIC_API_KEY": KEY,
        "LLM_API_KEY": KEY, "LLM_BASE_URL": "http://127.0.0.1:11434/v1",
    }
    llm = create_llm([TOOL], provider=provider, model=model, values=values)
    response = asyncio.run(llm.ainvoke([HumanMessage(content="Find CP-1")]))
    assert [(call["name"], call["args"]) for call in response.tool_calls] == [
        (CALL["name"], CALL["args"]),
    ]
    assert len(seen) == 1


@pytest.mark.parametrize("service,values,expected", [
    ("jira", JIRA, JIRA_TOOLS), ("github", GITHUB, GITHUB_TOOLS),
])
def test_mcp_stdio_tool_registrations(service, values, expected):
    async def discover():
        process = StdioServerParameters(
            command=sys.executable,
            args=[str(config.ENV_FILE.parent / "mcp" / f"{service}_mcp_server.py")],
            env={**values, "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                 "CAPSTONE_ENV_FILE": os.environ["CAPSTONE_ENV_FILE"]},
        )
        async with (
            stdio_client(process) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
        return {tool.name for tool in tools if tool.input_schema["type"] == "object"}

    assert asyncio.run(asyncio.wait_for(discover(), 15)) == expected


@pytest.mark.parametrize("service,values,call,response,expected", [
    ("jira", JIRA, "list_open_jiras", {
        "issues": [{"key": "CP-1", "fields": {"summary": "Test", "issuetype": {"name": "Task"},
                                               "status": {"name": "Open"}}}],
    }, "CP-1"),
    ("github", GITHUB, "list_repo_files", {
        "tree": [{"path": "Tool.java", "type": "blob"}],
    }, "Tool.java"),
])
def test_rest_result_contract(monkeypatch, service, values, call, response, expected):
    monkeypatch.setattr(config, "settings", lambda: values)
    server = importlib.import_module(f"{service}_mcp_server")
    request = Mock(return_value=Mock(status_code=200, json=lambda: response))
    monkeypatch.setattr(server.requests, "get", request)
    result = getattr(server, call)()
    assert isinstance(result, str)
    assert expected in result
    assert request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("module,entry,values,tool", [
    ("ai_orchestrator", "run_ai_orchestrator", JIRA, "create_jira_ticket"),
    ("github_orchestrator", "run_github_pr_workflow", GITHUB, "create_pull_request"),
])
def test_orchestrator_uses_common_factory_and_never_replays_write(
    monkeypatch, module, entry, values, tool,
):
    orchestrator = importlib.import_module(module)
    response = AIMessage(content="", tool_calls=[dict(CALL, name=tool)])
    llm, (primary, fallback) = adapters(response, AIMessage(content="unused"))
    primary.side_effect = [response, ProviderFailure(503)]
    registered = SimpleNamespace(name=tool, description="write", input_schema={"type": "object"})
    session = SimpleNamespace(
        initialize=AsyncMock(), list_tools=AsyncMock(return_value=SimpleNamespace(tools=[registered])),
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text="created")])),
    )

    @asynccontextmanager
    async def client(params, **_):
        assert "OPENAI_API_KEY" not in params.env
        yield None, None

    @asynccontextmanager
    async def session_context(*_):
        yield session

    mock_factory = Mock(return_value=llm)
    monkeypatch.setattr(orchestrator, "settings", lambda: dict(values, OPENAI_API_KEY=KEY))
    monkeypatch.setattr(orchestrator, "stdio_client", client)
    monkeypatch.setattr(orchestrator, "ClientSession", session_context)
    monkeypatch.setattr(orchestrator, "get_llm_with_tools", mock_factory)

    with pytest.raises(LLMError, match="after a write tool was attempted"):
        asyncio.run(getattr(orchestrator, entry)(provider="auto", model="chosen"))
    assert mock_factory.call_args.args[0][0]["function"]["parameters"] == registered.input_schema
    assert mock_factory.call_args.kwargs["provider"] == "auto"
    assert mock_factory.call_args.kwargs["model"] == "chosen"
    session.call_tool.assert_awaited_once_with(tool, arguments=CALL["args"])
    assert primary.await_args.args[0][-1].content == "created"
    fallback.assert_not_awaited()


def test_cli_report_and_exception_are_sanitized(capsys):
    previous_disable = logging.root.manager.disable
    try:
        assert launch.run(AsyncMock(side_effect=ValueError(KEY)), []) == 1
    finally:
        logging.disable(previous_disable)
    launch.print_report(f"echoed {KEY}", {"JIRA_API_TOKEN": KEY})
    output = capsys.readouterr()
    assert "[redacted]" in output.out
    assert KEY not in output.out + output.err
    assert "Traceback" not in output.err


def test_mcp_requires_initial_tool_call():
    llm = ToolLLM(
        [("openai_compatible", SimpleNamespace(ainvoke=AsyncMock(
            return_value=AIMessage(content="I skipped the tools"),
        )), 2)],
        require_initial_tool_call=True,
    )
    with pytest.raises(LLMError, match="Select a tool-capable model"):
        asyncio.run(llm.ainvoke([HumanMessage(content="Use the tool")]))


def test_wire_gemini_rate_limit_uses_openai_once(monkeypatch):
    hosts = []

    async def send(_client, request, **_kwargs):
        hosts.append(request.url.host)
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(429, request=request, json={
                "error": {"code": 429, "message": KEY, "status": "RESOURCE_EXHAUSTED"},
            })
        assert request.url.host == "api.openai.com"
        assert json.loads(request.content)["messages"][0]["content"] == "Use the same prompt"
        return httpx.Response(200, request=request, json={
            "id": "completion", "object": "chat.completion", "created": 1, "model": "gpt-4o-mini",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "fallback"}}],
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    llm = create_llm([TOOL], provider="auto", values={"OPENAI_API_KEY": KEY, "GOOGLE_API_KEY": KEY})
    assert asyncio.run(llm.ainvoke([HumanMessage(content="Use the same prompt")])).text == "fallback"
    assert hosts == ["generativelanguage.googleapis.com", "api.openai.com"]
