"""Test one configured LLM using a synthetic tool and no service credentials."""

import time

from config import ConfigurationError
from langchain_core.messages import HumanMessage, ToolMessage
from llm_provider import LLMError, get_llm_with_tools

TOOL_NAME = "lookup_fixture_status"
TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Return the status of a mock issue.",
        "parameters": {"type": "object", "properties": {}},
    },
}


async def check_provider(values):
    # Limit the request to synthetic data and the provider chosen in the wizard.
    start = time.perf_counter()
    messages = [HumanMessage(content=(
        f"Call {TOOL_NAME} once. Then give the issue key and status from the tool result."
    ))]
    try:
        model = get_llm_with_tools([TOOL], values=values)
        response = await model.ainvoke(messages)
        if len(response.tool_calls) != 1 or response.tool_calls[0]["name"] != TOOL_NAME:
            raise LLMError("The model did not request the expected mock tool once.")
        messages.extend([
            response,
            ToolMessage(
                content="Mock issue DEMO-947 is In Progress.",
                tool_call_id=response.tool_calls[0]["id"],
                name=TOOL_NAME,
            ),
        ])
        report = await model.ainvoke(messages)
        if report.tool_calls or "DEMO-947" not in report.text or "in progress" not in report.text.lower():
            raise LLMError("The model did not report the mock issue and status correctly.")
    except ConfigurationError as exc:
        raise RuntimeError(str(exc)) from None
    except LLMError as exc:
        raise RuntimeError(
            f"LLM test failed. {exc} Check the model, API access or local server and retry setup."
        ) from None
    print(f"LLM test passed. One mock tool call in {time.perf_counter() - start:.2f}s.")
