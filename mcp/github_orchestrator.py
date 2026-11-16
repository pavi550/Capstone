import asyncio
import os
import sys
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, ToolMessage
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


async def run_github_pr_workflow():
    for env_var in ("GOOGLE_API_KEY", "GITHUB_TOKEN"):
        if not os.getenv(env_var):
            print(f"❌ ERROR: {env_var} is not set!")
            return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(base_dir, "github_mcp_server.py")
    err_log_path = os.path.join(base_dir, "github_server_err.log")

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONUTF8"] = "1"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-u", server_script],
        env=child_env
    )

    try:
        with open(err_log_path, "w", encoding="utf-8") as err_file:
            async with stdio_client(server_params, errlog=err_file) as (read_stream, write_stream):
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

                    print(f"🔌 Connected to GitHub MCP Server! Learned {len(llm_tools)} tools.")

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
                    You are an autonomous AI-SDLC developer working on repository pavi550/Capstone.
                    1. Use `list_repo_files` (with extension="") to inspect the repository structure.
                    2. Use `read_file` to understand the existing files and architecture.
                    """

                    messages = [HumanMessage(content=prompt)]
                    print("\n🤖 Gemini is inspecting pavi550/Capstone and preparing a PR...\n")

                    while True:
                        response = await llm_with_tools.ainvoke(messages)
                        messages.append(response)

                        if not response.tool_calls:
                            print("\n✅ Final GitHub PR Agent Report:")
                            print(response.content)
                            break

                        for tool_call in response.tool_calls:
                            t_name = tool_call["name"]
                            print(f"   ⚙️ Gemini executing MCP Tool: {t_name}")

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
            with open(err_log_path, "r", encoding="utf-8") as f:
                server_stderr = f.read().strip()
            if server_stderr:
                print("\n🚨 Subprocess Server Error (github_server_err.log):")
                print(server_stderr)
        raise


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_github_pr_workflow())
