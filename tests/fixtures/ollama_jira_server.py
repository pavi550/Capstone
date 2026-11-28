"""Read-only stdio server for the Ollama Jira integration test."""

from typing import Literal

from mcp.server.mcpserver import MCPServer

server = MCPServer("Mock Jira")


@server.tool()
def list_open_jiras(project_key: Literal["CP"] = "CP") -> str:
    """List all open Jira tickets in project CP."""
    return "CP-8471 | Task | In Progress | Replace the quartz lantern"


if __name__ == "__main__":
    server.run(transport="stdio")
