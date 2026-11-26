"""Jira context lane: pull work items as requirements and raise regression defects."""

from __future__ import annotations

import json
import re
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import urlencode, urlsplit

from .config import Config
from .httpclient import HttpError, basic_auth, request_json
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


def adf_text(node: dict) -> str:
    """Flatten Jira paragraphs, headings and list items without executing rich content."""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    children = [adf_text(child) for child in node.get("content", []) if isinstance(child, dict)]
    if kind == "listItem":
        return "- " + " ".join(children)
    separator = "" if kind in {"paragraph", "heading", "codeBlock"} else "\n"
    return separator.join(children)


def _description_links(value):
    if isinstance(value, str):
        return re.findall(r'https://[^\s<>"\)]+', value)
    if isinstance(value, dict):
        links = [value[key] for key in ("href", "url") if isinstance(value.get(key), str)]
        return links + [link for child in value.values() for link in _description_links(child)]
    if isinstance(value, list):
        return [link for item in value for link in _description_links(item)]
    return []


def issue_to_requirement(issue: dict[str, Any]) -> Requirement:
    fields = issue.get("fields", {})
    description = fields.get("description") or ""
    if not isinstance(description, str):  # Jira Cloud v3 Atlassian Document Format
        description = adf_text(description) if isinstance(description, dict) else ""
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
        parsed = urlsplit(self.base)
        trusted = parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (not trusted or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment or "\\" in self.base):
            raise ValueError("jira.base_url must use HTTPS or loopback HTTP without embedded credentials")
        self.headers = basic_auth(user, token)
        self.cfg = cfg

    def search(self, jql: str, max_results: int = 50) -> list[dict[str, Any]]:
        path = self.cfg.get("jira.search_path", "/rest/api/2/search")
        if type(max_results) is not int or not 1 <= max_results <= 1000:
            raise ValueError("jira.max_results must be an integer between 1 and 1000")
        fields = "summary,description,issuetype,priority,status,attachment,issuelinks,updated"
        params = {"jql": jql, "fields": fields}
        issues = []
        seen_tokens = set()
        start = 0
        for _ in range(max_results + 1):
            params["maxResults"] = min(50, max_results - len(issues))
            data = self._search_page(f"{self.base}{path}?{urlencode(params)}")
            page = data.get("issues", [])
            issues.extend(page[:max_results - len(issues)])
            if len(issues) >= max_results or data.get("isLast"):
                return issues
            if path.endswith("/search/jql"):
                token = data.get("nextPageToken")
                if not token:
                    if data.get("isLast") is False:
                        raise RuntimeError("Jira returned a non-final page without a continuation token")
                    return issues
                if token in seen_tokens:
                    raise RuntimeError("Jira repeated a pagination token")
                seen_tokens.add(token)
                params["nextPageToken"] = token
            else:
                start += len(page)
                if not page or start >= data.get("total", start):
                    return issues
                params["startAt"] = start
        raise RuntimeError("Jira search exceeded its pagination limit")

    def get_requirement(self, issue_key: str) -> Requirement:
        return issue_to_requirement(self.get_issue(issue_key))

    def get_issue(self, issue_key: str) -> dict:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", issue_key):
            raise ValueError("Use a Jira issue key, e.g. CP-101")
        version = 3 if self.cfg.get("jira.search_path", "").startswith("/rest/api/3/") else 2
        data = self._search_page(
            f"{self.base}/rest/api/{version}/issue/{issue_key}?fields=summary,description,updated",
        )
        if data.get("key") != issue_key:
            raise RuntimeError("Jira returned a different issue key")
        return data

    def documentation_links(self, issue: dict) -> list[str]:
        key = issue["key"]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", key):
            raise ValueError("Invalid Jira issue key")
        remote = self._search_page(f"{self.base}/rest/api/3/issue/{key}/remotelink")
        if not isinstance(remote, list):
            raise TypeError("Jira returned an invalid remote links response")
        urls = _description_links(issue.get("fields", {}).get("description"))
        urls.extend(row.get("object", {}).get("url") for row in remote)
        return list(dict.fromkeys(url for url in urls if isinstance(url, str)))

    def _search_page(self, url):
        for attempt in range(3):
            try:
                return request_json("GET", url, headers=self.headers)
            except HttpError as exc:
                if exc.status not in {429, 502, 503, 504} or attempt == 2:
                    raise RuntimeError(f"Jira search failed (HTTP {exc.status})") from None
            sleep(0.25 * 2 ** attempt)

    def transition(self, issue_key: str, stage: str) -> dict:
        if not self.cfg.get("enterprise.jira_write.enabled", False):
            raise ValueError("Jira transitions are disabled by policy")
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", issue_key)
                or issue_key.rsplit("-", 1)[0] not in self.cfg.get("enterprise.jira_write.projects", [])):
            raise ValueError("Jira write target is outside the authorized project scope")
        mappings = self.cfg.get("enterprise.jira_write.transitions", {})
        transition = mappings.get(stage) if isinstance(mappings, dict) else None
        if not isinstance(transition, str) or not re.fullmatch(r"[0-9]+", transition):
            raise ValueError("Jira stage has no authorized transition mapping")
        try:
            request_json("POST", f"{self.base}/rest/api/3/issue/{issue_key}/transitions", headers=self.headers,
                         body={"transition": {"id": transition}})
        except HttpError as exc:
            raise RuntimeError(f"Jira transition failed (HTTP {exc.status})") from None
        return {"issue": issue_key, "stage": stage, "status": "TRANSITION_REQUESTED"}

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
