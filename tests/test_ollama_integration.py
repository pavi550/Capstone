"""Exercise the production Jira loop with only a fixture MCP tool available."""

import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

import ai_orchestrator
import httpx
import pytest

BASE_URL = "http://127.0.0.1:11434/v1"
MODEL = "qwen3:8b"
TOOL_NAME = "list_open_jiras"
ISSUE_KEY = "CP-8471"
CALL_ID = "call_mock_jira"
SERVER = Path(__file__).parent / "fixtures" / "ollama_jira_server.py"
VALUES = {
    "JIRA_BASE_URL": "https://jira.example.invalid",
    "JIRA_EMAIL": "fixture@example.invalid",
    "JIRA_API_TOKEN": "fixture-only",
    "LLM_PROVIDER": "openai_compatible",
    "LLM_BASE_URL": BASE_URL,
    "LLM_API_KEY": "ollama",
    "LLM_MODEL": MODEL,
    "LLM_TEMPERATURE": "0",
    "LLM_TIMEOUT_SECONDS": "180",
}


def run_jira_with_fixture(monkeypatch, capsys):
    server_parameters = ai_orchestrator.StdioServerParameters

    def fixture_parameters(*, command, args, env):
        return server_parameters(command=command, args=["-u", str(SERVER)], env={
            "PYTHONUTF8": "1",
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "CAPSTONE_ENV_FILE": os.environ["CAPSTONE_ENV_FILE"],
        })

    monkeypatch.setattr(ai_orchestrator, "settings", lambda: VALUES.copy())
    monkeypatch.setattr(ai_orchestrator, "StdioServerParameters", fixture_parameters)
    start = perf_counter()
    asyncio.run(asyncio.wait_for(ai_orchestrator.run_ai_orchestrator(), timeout=370))
    elapsed = perf_counter() - start
    output = capsys.readouterr().out
    assert output.count(f"Executing MCP Tool: {TOOL_NAME}") == 1
    _, separator, report = output.partition("Final AI Project Manager Report:")
    assert separator
    assert ISSUE_KEY in report
    assert "in progress" in report.lower()
    return elapsed


def test_ollama_adapter_with_mocked_chat_completions(monkeypatch, capsys):
    requests = []

    async def send(_client, request, **_kwargs):
        assert str(request.url) == f"{BASE_URL}/chat/completions"
        assert request.headers["authorization"] == "Bearer ollama"
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        assert [tool["function"]["name"] for tool in payload["tools"]] == [TOOL_NAME]
        requests.append(payload)
        if len(requests) == 1:
            assert ISSUE_KEY not in json.dumps(payload["messages"])
            finish = "tool_calls"
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": CALL_ID,
                "type": "function",
                "function": {"name": TOOL_NAME, "arguments": '{"project_key":"CP"}'},
            }]}
        else:
            tool_result = payload["messages"][-1]
            assert tool_result["role"] == "tool"
            assert tool_result["tool_call_id"] == CALL_ID
            assert ISSUE_KEY in tool_result["content"]
            assert "In Progress" in tool_result["content"]
            finish = "stop"
            message = {"role": "assistant", "content": f"{ISSUE_KEY} is In Progress."}
        return httpx.Response(200, request=request, json={
            "id": "ollama-fixture",
            "object": "chat.completion",
            "created": 1,
            "model": MODEL,
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    run_jira_with_fixture(monkeypatch, capsys)
    assert len(requests) == 2


@pytest.mark.skipif(os.environ.get("CAPSTONE_TEST_OLLAMA") != "1",
                    reason="set CAPSTONE_TEST_OLLAMA=1 to run against local Ollama")
def test_ollama_live_tool_round_trip(monkeypatch, capsys):
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            models = client.get(f"{BASE_URL}/models", headers={"Authorization": "Bearer ollama"})
            models.raise_for_status()
    except httpx.HTTPError:
        pytest.skip("Local Ollama unavailable. Start it with: ollama serve")
    if MODEL not in {model["id"] for model in models.json()["data"]}:
        pytest.skip(f"{MODEL} is unavailable locally. Install it with: ollama pull {MODEL}")

    elapsed = run_jira_with_fixture(monkeypatch, capsys)
    print(f"Ollama {MODEL}: 1/1 read-only tool calls; end-to-end latency {elapsed:.2f}s")
