import asyncio
import os
import sys
from langchain_core.messages import HumanMessage, ToolMessage
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from llm_provider import get_llm_with_tools


async def run_github_pr_workflow():
    if not os.getenv("GITHUB_TOKEN"):
        print("❌ ERROR: GITHUB_TOKEN is not set!")
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
                    llm_with_tools = get_llm_with_tools(llm_tools)

                    prompt = """
                    You are an autonomous AI-SDLC developer working on repository pavi550/Capstone.
                    1. Use `list_repo_files` (with extension="") to inspect the repository structure.
                    2. Use `read_file` to understand the existing files and architecture.
                    3. Create a new feature branch named `feat/mcp-github-pr-demo` using `create_branch`.
                    4. Add a clean `ARCHITECTURE.md` file (or update an existing file) using `commit_file_change` documenting the MCP GitHub & Jira SDLC workflow.
                    5. Raise a Pull Request into `main` using `create_pull_request` with a clear summary of changes, and share the PR link.
                    """

                    messages = [HumanMessage(content=prompt)]
                    print("\n🤖 Inspecting pavi550/Capstone and preparing a PR...\n")

                    while True:
                        response = await llm_with_tools.ainvoke(messages)
                        messages.append(response)

                        if not response.tool_calls:
                            print("\n✅ Final GitHub PR Agent Report:")
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
