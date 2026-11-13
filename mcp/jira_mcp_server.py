import os
import requests
from requests.auth import HTTPBasicAuth
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("Capstone-Jira-Server")

DOMAIN = "https://capstoneprojectgrp4.atlassian.net"
EMAIL = "pes2pge25ds030@stu.pes.edu"

# Reads from JIRA_API_TOKEN environment variable first, falling back to the hardcoded string
API_TOKEN = os.getenv("JIRA_API_TOKEN", "YOUR_ACTIVE_JIRA_TOKEN").strip()

# Fail immediately at server startup if the token is missing or still a placeholder
if not API_TOKEN or API_TOKEN in ("YOUR_ACTIVE_JIRA_TOKEN", "CHANGE_ME"):
    raise RuntimeError(
        "CRITICAL: JIRA_API_TOKEN is not updated. Set the JIRA_API_TOKEN environment variable "
        "or replace YOUR_ACTIVE_JIRA_TOKEN in jira_mcp_server.py."
    )

HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)


def _to_adf(text: str) -> dict:
    """Convert plain text into Atlassian Document Format (ADF) required by Jira API v3."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}]
            }
        ]
    }


def _check_jira_response(response: requests.Response, expected_status: int, action_label: str):
    """Raise an actionable MCP ToolError on auth failure (401/403) or unexpected HTTP codes."""
    if response.status_code in (401, 403):
        raise ToolError(
            f"Jira Authentication Failed (HTTP {response.status_code}): "
            "Your JIRA_API_TOKEN is invalid, expired, or lacks project permissions. Please update it."
        )
    if response.status_code != expected_status:
        raise ToolError(
            f"Jira API Error while {action_label} (HTTP {response.status_code}): {response.text}"
        )


@mcp.tool()
def list_open_jiras(project_key: str = "CP") -> str:
    """Fetch all open Jira tickets and their current statuses for a specific project."""
    url = f"{DOMAIN}/rest/api/3/search/jql"
    query = {
        "jql": f"project = {project_key} AND statusCategory != Done ORDER BY updated DESC",
        "fields": "summary,status",
        "maxResults": 20
    }
    response = requests.get(url, headers={"Accept": "application/json"}, params=query, auth=AUTH, timeout=15)
    _check_jira_response(response, 200, f"listing open Jiras for {project_key}")

    issues = response.json().get("issues", [])
    if not issues:
        return f"No open tickets found in {project_key}."

    return "\n".join(
        f"- {i.get('key')} [{i.get('fields', {}).get('status', {}).get('name', 'Unknown')}]: "
        f"{i.get('fields', {}).get('summary', '')}"
        for i in issues
    )


@mcp.tool()
def create_jira_ticket(summary: str, description: str, project_key: str = "CP", issue_type: str = "Task") -> str:
    """Create a new Jira ticket. Use this to add features, bugs, or tasks to the backlog."""
    url = f"{DOMAIN}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": _to_adf(description),
            "issuetype": {"name": issue_type}
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload, auth=AUTH, timeout=15)
    _check_jira_response(response, 201, f"creating ticket '{summary}'")

    return f"Successfully created ticket: {response.json().get('key')} - {summary}"


@mcp.tool()
def update_jira_fields(issue_key: str, summary: str = "", description: str = "") -> str:
    """Update the summary (title) and/or description of an existing Jira ticket (e.g., 'CP-2')."""
    fields = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = _to_adf(description)

    if not fields:
        raise ToolError("No fields provided to update. Pass a new summary or description.")

    url = f"{DOMAIN}/rest/api/3/issue/{issue_key}"
    response = requests.put(url, headers=HEADERS, json={"fields": fields}, auth=AUTH, timeout=15)
    _check_jira_response(response, 204, f"updating fields for {issue_key}")

    return f"Successfully updated fields for {issue_key}."


@mcp.tool()
def add_jira_comment(issue_key: str, comment_text: str) -> str:
    """Add a comment to an existing Jira ticket (e.g., code review notes, PR links, or AI agent logs)."""
    url = f"{DOMAIN}/rest/api/3/issue/{issue_key}/comment"
    payload = {"body": _to_adf(comment_text)}
    response = requests.post(url, headers=HEADERS, json=payload, auth=AUTH, timeout=15)
    _check_jira_response(response, 201, f"adding comment to {issue_key}")

    return f"Successfully added comment to {issue_key}."


@mcp.tool()
def update_jira_status(issue_key: str, target_status: str) -> str:
    """Move a Jira ticket to a new workflow status (e.g., 'To Do', 'In Progress', 'Done')."""
    url = f"{DOMAIN}/rest/api/3/issue/{issue_key}/transitions"

    get_res = requests.get(url, headers={"Accept": "application/json"}, auth=AUTH, timeout=15)
    _check_jira_response(get_res, 200, f"fetching workflow transitions for {issue_key}")

    transitions = get_res.json().get("transitions", [])
    matched_id = None
    available_names = []

    for t in transitions:
        t_name = t.get("name", "")
        to_name = t.get("to", {}).get("name", "")
        available_names.append(to_name or t_name)
        if target_status.strip().lower() in (t_name.lower(), to_name.lower()):
            matched_id = t.get("id")
            break

    if not matched_id:
        raise ToolError(
            f"Invalid status '{target_status}' for {issue_key}. "
            f"Available transitions: {', '.join(available_names)}"
        )

    post_res = requests.post(url, headers=HEADERS, json={"transition": {"id": matched_id}}, auth=AUTH, timeout=15)
    _check_jira_response(post_res, 204, f"moving {issue_key} to '{target_status}'")

    return f"Successfully moved {issue_key} to '{target_status}'."


if __name__ == "__main__":
    mcp.run()
