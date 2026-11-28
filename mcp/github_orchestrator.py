import asyncio
import os
import sys

from config import GITHUB_VARIABLES, github_settings, settings
from langchain_core.messages import HumanMessage, ToolMessage
from launch import print_report, run
from llm_provider import get_llm_with_tools
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

READ_ONLY_TOOLS = {"list_repo_files", "search_repo_code", "read_file"}


async def run_github_pr_workflow(*, provider=None, model=None):
    values = settings()
    _, owner, repo, branch = github_settings(values)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(base_dir, "github_mcp_server.py")
    child_env = {name: values[name] for name in GITHUB_VARIABLES if name in values}
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

            print(f"🔌 Connected to GitHub MCP Server! Learned {len(llm_tools)} tools.")
            llm_with_tools = get_llm_with_tools(
                llm_tools, provider=provider, model=model, values=values,
            )

            prompt = f"""
            You are an autonomous AI-SDLC developer working on repository {owner}/{repo}.
            1. Use `list_repo_files` (with extension="") to inspect the repository structure.
            2. Use `read_file` to understand the existing files and architecture.
            3. Create a new feature branch named `feat/mcp-github-pr-demo` using `create_branch`.
            4. Add a clean `ARCHITECTURE.md` file (or update an existing file) using `commit_file_change` documenting the MCP GitHub & Jira SDLC workflow.
            5. Raise a Pull Request into `{branch}` using `create_pull_request` with a clear summary of changes, and share the PR link.
            """

            messages = [HumanMessage(content=prompt)]
            print(f"\n🤖 Inspecting {owner}/{repo} and preparing a PR...\n")

            while True:
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                if not response.tool_calls:
                    print("\n✅ Final GitHub PR Agent Report:")
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
    sys.exit(run(run_github_pr_workflow))
