import asyncio
import sys
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, ToolMessage
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def run_ai_orchestrator():
    if not os.getenv("GOOGLE_API_KEY"):
        print("❌ ERROR: You must set your GOOGLE_API_KEY environment variable first!")
        return

    # Dynamically resolve jira_mcp_server.py in the same folder as ai_orchestrator.py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    SERVER_SCRIPT = os.path.join(BASE_DIR, "jira_mcp_server.py")

    server_params = StdioServerParameters(command=sys.executable, args=["SERVER_SCRIPT"])
    
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            mcp_tools = await session.list_tools()
            
            llm_tools = []
            for tool in mcp_tools.tools:
                llm_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        # THE FIX: Updated to v2.0 snake_case
                        "parameters": tool.input_schema 
                    }
                })
            
            print(f"🔌 Connected! Gemini dynamically learned {len(llm_tools)} tools via MCP.")
            
            llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", temperature=0, max_retries=1, timeout=30)
            llm_with_tools = llm.bind_tools(llm_tools)
            
            prompt = """
            You are my AI-SDLC project manager. Please do below things:
            project key is CP
            get the list of jiras and understand the summary/subject. update the detailed description of the implementation of task for all jiras.            
            """
            
            messages = [HumanMessage(content=prompt)]
            print("\n🤖 Gemini is thinking and orchestrating the workflow...\n")
            
            while True:
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)
                
                if not response.tool_calls:
                    print("\n✅ Final AI Project Manager Report:")
                    print(response.content)
                    break
                    
                for tool_call in response.tool_calls:
                    print(f"   ⚙️ Gemini decided to execute MCP Tool: {tool_call['name']}")
                    
                    mcp_result = await session.call_tool(tool_call["name"], arguments=tool_call["args"])
                    result_text = "\n".join([item.text for item in mcp_result.content])
                    
                    messages.append(ToolMessage(
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                        content=result_text
                    ))

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_ai_orchestrator())
