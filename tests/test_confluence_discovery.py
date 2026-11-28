import json
from urllib.parse import parse_qs, urlsplit

import pytest

from agentic_sdlc import confluence, jira
from agentic_sdlc.cli import main
from agentic_sdlc.config import Config

SITE = "https://test.atlassian.net"


def config(monkeypatch):
    for name, value in {
        "JIRA_BASE_URL": SITE, "JIRA_EMAIL": "reader@example.org", "JIRA_API_TOKEN": "jira-test",
        "CONFLUENCE_BASE_URL": SITE, "CONFLUENCE_EMAIL": "reader@example.org", "CONFLUENCE_API_TOKEN": "wiki-test",
    }.items():
        monkeypatch.setenv(name, value)
    return Config.load(enterprise_intake=True)


def test_cursor_cannot_send_credentials_outside_original_endpoint(monkeypatch):
    cfg = config(monkeypatch)
    requested = []

    def request(method, url, **kwargs):
        requested.append(url)
        return {"results": [{"id": "1"}], "_links": {"next": "https://other.test/wiki/api/v2/spaces?cursor=bad"}}

    monkeypatch.setattr(confluence, "request_json", request)
    with pytest.raises(RuntimeError, match="pagination"):
        confluence.ConfluenceClient(cfg).spaces()
    assert requested == [f"{SITE}/wiki/api/v2/spaces?limit=100"]


def test_space_pagination_and_literal_keyword_search(monkeypatch, capsys):
    config(monkeypatch)

    def request(method, url, **kwargs):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/spaces"):
            if query.get("cursor") == ["next"]:
                return {"results": [{"id": "2", "key": "SECOND"}]}
            return {"results": [{"id": "1", "key": "FIRST"}],
                    "_links": {"next": "/wiki/api/v2/spaces?cursor=next"}}
        assert query["cql"] == ['type = page AND text ~ "coding standards"']
        return {"results": [{"id": "123", "title": "Example"}]}

    monkeypatch.setattr(confluence, "request_json", request)
    assert main(["dev", "context", "--spaces"]) == 0
    assert len(json.loads(capsys.readouterr().out)["spaces"]) == 2
    assert main(["dev", "context", "--search", "coding standards"]) == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["id"] == "123"


def test_jira_linked_page_is_automatically_fetched_for_spec(monkeypatch, capsys):
    config(monkeypatch)

    def request_jira(method, url, **kwargs):
        if url.endswith("/remotelink"):
            return [{"object": {"url": f"{SITE}/wiki/spaces/CP/pages/123/Project"}},
                    {"object": {"url": "https://other.test/wiki/spaces/CP/pages/124/Project"}}]
        return {"key": "CP-4", "fields": {"summary": "Example", "updated": "revision",
                "description": "Acceptance Criteria:\n- Works"}}

    def request_wiki(method, url, **kwargs):
        assert url == f"{SITE}/wiki/api/v2/pages/123?body-format=storage"
        return {"id": "123", "title": "Example", "version": {"number": 3},
                "body": {"storage": {"value": "untrusted example"}}}

    monkeypatch.setattr(jira, "request_json", request_jira)
    monkeypatch.setattr(confluence, "request_json", request_wiki)
    assert main(["dev", "spec", "CP-4"]) == 0
    output = capsys.readouterr().out
    spec = json.loads(output)
    assert spec["confluence"][0]["version"] == 3
    assert spec["jira_revision"] == "revision"
    assert spec["traceability"]["CP-4-AC-1"]["url"].endswith("/browse/CP-4")
    assert "untrusted example" not in output
