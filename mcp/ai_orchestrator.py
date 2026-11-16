import asyncio
import os
import sys
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, ToolMessage
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


async def run_ai_orchestrator():
    for env_var in ("GOOGLE_API_KEY", "JIRA_API_TOKEN"):
        if not os.getenv(env_var):
            print(f"ERROR: {env_var} is not set in your terminal!")
            return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(base_dir, "jira_mcp_server.py")

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONUTF8"] = "1"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-u", server_script],
        env=child_env
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
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

            print(f"Connected! Gemini dynamically learned {len(llm_tools)} tools via MCP.")

            primary_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                temperature=0,
                max_retries=2,
                timeout=120
            ).bind_tools(llm_tools)

            fallback_llm = ChatGoogleGenerativeAI(
                model="gemini-3.8-pro",
                temperature=0,
                max_retries=2,
                timeout=120
            ).bind_tools(llm_tools)

            llm_with_tools = primary_llm.with_fallbacks([fallback_llm])

            prompt = """
            You are an AI Project Manager for project CP.
            1. Call `list_open_jiras` to check all currently open tickets in project CP.
            2. Summarize the open tickets clearly with their keys, issue types, and current statuses.
            """

            messages = [HumanMessage(content=prompt)]
            print("\nGemini is thinking and orchestrating the Jira workflow...\n")

            while True:
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                if not response.tool_calls:
                    print("\nFinal AI Project Manager Report:")
                    print(response.content)
                    break

                for tool_call in response.tool_calls:
                    t_name = tool_call["name"]
                    print(f"   Gemini decided to execute MCP Tool: {t_name}")

                    mcp_result = await session.call_tool(t_name, arguments=tool_call["args"])
                    result_text = "\n".join(
                        item.text for item in mcp_result.content if hasattr(item, "text")
                    )

                    messages.append(ToolMessage(
                        tool_call_id=tool_call["id"],
                        name=t_name,
                        content=result_text
                    ))


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_ai_orchestrator())
