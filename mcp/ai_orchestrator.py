import asyncio
import os
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage
from llm_provider import get_llm_with_tools
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def run_ai_orchestrator():
    if not os.getenv("JIRA_API_TOKEN"):
        print("❌ ERROR: JIRA_API_TOKEN is not set in your terminal!")
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(base_dir, "jira_mcp_server.py")
    err_log_path = os.path.join(base_dir, "jira_server_err.log")

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONUTF8"] = "1"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-u", server_script],
        env=child_env
    )

    err_file = await asyncio.to_thread(open, err_log_path, "w", encoding="utf-8")
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
            llm_with_tools = get_llm_with_tools(llm_tools)

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
                    print(response.content)
                    break

                for tool_call in response.tool_calls:
                    t_name = tool_call["name"]
                    print(f"   ⚙️ Executing MCP Tool: {t_name}")

                    mcp_result = await session.call_tool(t_name, arguments=tool_call["args"])
                    result_text = "\n".join(
                        item.text for item in mcp_result.content if hasattr(item, "text")
                    )

                    messages.append(ToolMessage(
                        tool_call_id=tool_call["id"],
                        name=t_name,
                        content=result_text
                    ))
    except Exception:
        if os.path.exists(err_log_path):
            server_stderr = (
                await asyncio.to_thread(Path(err_log_path).read_text, encoding="utf-8")
            ).strip()
            if server_stderr:
                print("\n🚨 Subprocess Server Error (jira_server_err.log):")
                print(server_stderr)
        raise
    finally:
        await asyncio.to_thread(err_file.close)


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_ai_orchestrator())
