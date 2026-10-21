"""Jira context lane: pull work items as requirements and raise regression defects."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .config import Config
from .httpclient import basic_auth, request_json
from .state import Requirement

_BULLET = re.compile(r"^\s*(?:[-*•]|#|\d+[.)])\s+(.*)$")


def split_description(text: str) -> tuple[str, list[str]]:
    """Separate free text from bullets that follow an 'Acceptance criteria' line."""
    desc, criteria, in_ac = [], [], False
    for line in (text or "").splitlines():
        if re.search(r"acceptance criteria", line, re.IGNORECASE):
            in_ac = True
            continue
        bullet = _BULLET.match(line)
        if in_ac and bullet:
            criteria.append(bullet.group(1).strip())
        elif line.strip():
            desc.append(line.strip())
    return "\n".join(desc), criteria


def issue_to_requirement(issue: dict[str, Any]) -> Requirement:
    fields = issue.get("fields", {})
    description = fields.get("description") or ""
    if not isinstance(description, str):  # Jira Cloud v3 Atlassian Document Format
        description = json.dumps(description)
    desc, criteria = split_description(description)
    return Requirement(
        id=issue["key"], title=fields.get("summary", issue["key"]), description=desc,
        acceptance_criteria=criteria, source="jira",
    )


class JiraClient:
    def __init__(self, cfg: Config):
        self.base = (cfg.get("jira.base_url") or "").rstrip("/")
        user, token = cfg.secret("jira.user_env"), cfg.secret("jira.token_env")
        if not self.base or not user or not token:
            raise RuntimeError("jira.base_url and the JIRA user/token env vars must be set")
        self.headers = basic_auth(user, token)
        self.cfg = cfg

    def search(self, jql: str, max_results: int = 50) -> list[dict[str, Any]]:
        path = self.cfg.get("jira.search_path", "/rest/api/2/search")
        query = urlencode({"jql": jql, "maxResults": max_results, "fields": "summary,description"})
        data = request_json("GET", f"{self.base}{path}?{query}", headers=self.headers)
        return data.get("issues", [])

    def create_issue(self, project: str, summary: str, description: str, issuetype: str) -> str:
        body = {"fields": {
            "project": {"key": project}, "summary": summary,
            "description": description, "issuetype": {"name": issuetype},
        }}
        data = request_json("POST", f"{self.base}/rest/api/2/issue", headers=self.headers, body=body)
        return data["key"]


def fetch_requirements(cfg: Config) -> list[Requirement]:
    """Requirements from Jira: offline JSON export if configured, else live REST when enabled."""
    issues_file = cfg.get("jira.issues_file")
    if issues_file:
        data = json.loads(Path(issues_file).read_text(encoding="utf-8"))
        issues = data.get("issues", data) if isinstance(data, dict) else data
        return [issue_to_requirement(i) for i in issues]
    if cfg.get("jira.enabled") and cfg.get("jira.jql"):
        client = JiraClient(cfg)
        return [issue_to_requirement(i) for i in
                client.search(cfg.get("jira.jql"), cfg.get("jira.max_results", 50))]
    return []
