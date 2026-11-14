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

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script]
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

            print(f"🔌 Connected to GitHub MCP Server! Learned {len(llm_tools)} tools.")

            primary_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite", temperature=0, max_retries=2, timeout=120
            ).bind_tools(llm_tools)
            fallback_llm = ChatGoogleGenerativeAI(
                model="gemini-3.8-pro", temperature=0, max_retries=2, timeout=120
            ).bind_tools(llm_tools)
            llm_with_tools = primary_llm.with_fallbacks([fallback_llm])

            # Customize this prompt for the exact change you want Gemini to implement and PR
            prompt = """
            You are an autonomous AI-SDLC developer working on repository pavi550/Capstone.
            1. Use `list_repo_files` (with extension="") to inspect the repository structure.
            2. Use `read_file` to understand the existing files and architecture.
            3. Create a new feature branch named `feat/mcp-github-pr-demo` using `create_branch`.
            4. Add a clean `ARCHITECTURE.md` file (or update an existing file) using `commit_file_change` documenting the MCP GitHub & Jira SDLC workflow.
            5. Raise a Pull Request into `main` using `create_pull_request` with a clear summary of changes, and share the PR link.
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
                    result_text = "\n".join(item.text for item in mcp_result.content if hasattr(item, "text"))

                    messages.append(ToolMessage(
                        tool_call_id=tool_call["id"],
                        name=t_name,
                        content=result_text
                    ))


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_github_pr_workflow())
