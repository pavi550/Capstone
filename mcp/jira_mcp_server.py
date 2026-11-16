import os
import requests
from requests.auth import HTTPBasicAuth
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("Capstone-Jira-Server")

DOMAIN = "https://capstoneprojectgrp4.atlassian.net"
EMAIL = "pes2pge25ds030@stu.pes.edu"
API_TOKEN = os.getenv("JIRA_API_TOKEN", "ATATT3xFfGF0wrt2LMulOt6mUSfnWnilST-ifSUH2Frk0Pgo0fwSd5Uj_Q2z3vJmvxK9pgLf-6SXIthqUM_6X3HLIc3BNCYEA6sC_OqJZn1r26dYd2npUjCsU1mZsMqGeY1C2RrQOXuuzVLceteAG3n3Kug89UkFdathNYO1D7pi_XSExSFPw6w=63DDE854").strip()

if not API_TOKEN or API_TOKEN == "YOUR_ACTIVE_JIRA_TOKEN":
    raise RuntimeError(
        "CRITICAL: JIRA_API_TOKEN is not set. Export JIRA_API_TOKEN in your terminal before running."
    )

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}


def _make_adf(text: str) -> dict:
    """Convert plain text into Atlassian Document Format (ADF) required by Jira REST API v3."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": p}]
            }
            for p in paragraphs
        ]
    }


def _check_jira_response(response: requests.Response, expected_codes: tuple[int, ...], action: str):
    if response.status_code in (401, 403):
        raise ToolError(
            f"Jira Authentication Failed (HTTP {response.status_code}) while {action}. "
            "Verify JIRA_API_TOKEN and project permissions."
        )
    if response.status_code == 404:
        raise ToolError(f"Jira Resource Not Found (404) while {action}.")
    if response.status_code not in expected_codes:
        raise ToolError(f"Jira API Error while {action} (HTTP {response.status_code}): {response.text}")


@mcp.tool()
def list_open_jiras(project_key: str = "CP") -> str:
    """List all open (non-Done) Jira tickets in the specified project."""
    url = f"{DOMAIN}/rest/api/3/search/jql"
    params = {
        "jql": f"project = {project_key} AND status != Done ORDER BY created DESC",
        "fields": "summary,status,issuetype,priority",
        "maxResults": 50
    }
    response = requests.get(url, headers=HEADERS, auth=AUTH, params=params, timeout=15)
    _check_jira_response(response, (200,), f"listing open Jiras for {project_key}")

    issues = response.json().get("issues", [])
    if not issues:
        return f"No open Jira tickets found in project '{project_key}'."

    lines = []
    for issue in issues:
        key = issue.get("key")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "No summary")
        status = fields.get("status", {}).get("name", "Unknown")
        issue_type = fields.get("issuetype", {}).get("name", "Task")
        lines.append(f"- [{key}] ({issue_type} | {status}): {summary}")

    return "\n".join(lines)


@mcp.tool()
def create_jira_ticket(
    summary: str,
    description: str,
    project_key: str = "CP",
    issue_type: str = "Task"
) -> str:
    """Create a new Jira issue in Atlassian Document Format (ADF)."""
    url = f"{DOMAIN}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": _make_adf(description),
            "issuetype": {"name": issue_type}
        }
    }
    response = requests.post(url, headers=HEADERS, auth=AUTH, json=payload, timeout=15)
    _check_jira_response(response, (201,), f"creating Jira ticket '{summary}'")

    data = response.json()
    issue_key = data.get("key")
    return f"Successfully created Jira ticket {issue_key}: {DOMAIN}/browse/{issue_key}"


@mcp.tool()
def update_jira_fields(issue_key: str, summary: str = "", description: str = "") -> str:
    """Update the summary and/or description of an existing Jira ticket (e.g., 'CP-2')."""
    fields_to_update = {}
    if summary.strip():
        fields_to_update["summary"] = summary.strip()
    if description.strip():
        fields_to_update["description"] = _make_adf(description.strip())

    if not fields_to_update:
        raise ToolError("Provide at least one of 'summary' or 'description' to update.")

    url = f"{DOMAIN}/rest/api/3/issue/{issue_key.strip()}"
    response = requests.put(url, headers=HEADERS, auth=AUTH, json={"fields": fields_to_update}, timeout=15)
    _check_jira_response(response, (204,), f"updating fields on {issue_key}")

    return f"Successfully updated fields on {issue_key}."


@mcp.tool()
def add_jira_comment(issue_key: str, comment_text: str) -> str:
    """Add a comment (review notes, logs, or PR link) to an existing Jira ticket."""
    url = f"{DOMAIN}/rest/api/3/issue/{issue_key.strip()}/comment"
    payload = {"body": _make_adf(comment_text)}
    response = requests.post(url, headers=HEADERS, auth=AUTH, json=payload, timeout=15)
    _check_jira_response(response, (201,), f"adding comment to {issue_key}")

    return f"Successfully added comment to {issue_key}."


@mcp.tool()
def update_jira_status(issue_key: str, target_status: str) -> str:
    """Transition an existing Jira ticket to a new workflow status (e.g., 'To Do', 'In Progress', 'Done')."""
    clean_key = issue_key.strip()
    trans_url = f"{DOMAIN}/rest/api/3/issue/{clean_key}/transitions"
    trans_res = requests.get(trans_url, headers=HEADERS, auth=AUTH, timeout=15)
    _check_jira_response(trans_res, (200,), f"fetching transitions for {clean_key}")

    transitions = trans_res.json().get("transitions", [])
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
            f"Status '{target_status}' is not a valid transition for {clean_key}. "
            f"Available transitions: {', '.join(available_names)}"
        )

    post_res = requests.post(
        trans_url,
        headers=HEADERS,
        auth=AUTH,
        json={"transition": {"id": matched_id}},
        timeout=15
    )
    _check_jira_response(post_res, (204,), f"transitioning {clean_key} to '{target_status}'")

    return f"Successfully moved {clean_key} to '{target_status}'."


if __name__ == "__main__":
    mcp.run()
