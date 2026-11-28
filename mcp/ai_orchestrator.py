import asyncio
import os
import sys

from config import JIRA_VARIABLES, jira_settings, settings
from langchain_core.messages import HumanMessage, ToolMessage
from launch import print_report, run
from llm_provider import get_llm_with_tools
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

READ_ONLY_TOOLS = {"list_open_jiras"}


async def run_ai_orchestrator(*, provider=None, model=None):
    values = settings()
    jira_settings(values)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(base_dir, "jira_mcp_server.py")
    child_env = {name: values[name] for name in JIRA_VARIABLES if name in values}
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONUTF8"] = "1"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-u", server_script],
        env=child_env
    )

    err_file = await asyncio.to_thread(open, os.devnull, "w", encoding="utf-8")
    try:
        async with (
            stdio_client(server_params, errlog=err_file) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()

            mcp_tools = await session.list_tools()
            llm_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema
                    }
                }
                for tool in mcp_tools.tools
            ]

            print(f"🔌 Connected! Learned {len(llm_tools)} Jira tools via MCP.")
            llm_with_tools = get_llm_with_tools(
                llm_tools, provider=provider, model=model, values=values,
            )

            prompt = """
            You are an AI Project Manager for project CP.
            1. Call `list_open_jiras` to check all currently open tickets in project CP.
            2. Summarize the open tickets clearly with their keys, issue types, and current statuses.
            """

            messages = [HumanMessage(content=prompt)]
            print("\n🤖 Orchestrating the Jira workflow...\n")

            while True:
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                if not response.tool_calls:
                    print("\n✅ Final AI Project Manager Report:")
                    print_report(response.content, values)
                    break

                for tool_call in response.tool_calls:
                    t_name = tool_call["name"]
                    print(f"   ⚙️ Executing MCP Tool: {t_name}")

                    if t_name not in READ_ONLY_TOOLS:
                        llm_with_tools.mark_write_attempted()
                    mcp_result = await session.call_tool(t_name, arguments=tool_call["args"])
                    result_text = "\n".join(
                        item.text for item in mcp_result.content if hasattr(item, "text")
                    )

                    messages.append(ToolMessage(
                        tool_call_id=tool_call["id"],
                        name=t_name,
                        content=result_text
                    ))

    finally:
        await asyncio.to_thread(err_file.close)


if __name__ == "__main__":
    sys.exit(run(run_ai_orchestrator))
