from agentic_sdlc import confluence, jira
from agentic_sdlc.cli import main
from agentic_sdlc.config import Config
from agentic_sdlc.httpclient import basic_auth


def test_enterprise_reads_use_setup_dotenv_without_yaml(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAPSTONE_ENV_FILE")
    for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_JQL",
                 "CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "JIRA_BASE_URL=https://test.atlassian.net\nJIRA_EMAIL=reader@example.org\n"
        "JIRA_API_TOKEN='fixture-${LITERAL}'\nJIRA_JQL='project = CP'\n"
        "CONFLUENCE_BASE_URL=https://test.atlassian.net/wiki\n"
        "CONFLUENCE_EMAIL=reader@example.org\nCONFLUENCE_API_TOKEN=page-fixture\n",
        encoding="utf-8",
    )

    def search(method, url, *, headers):
        assert method == "GET"
        assert url.startswith("https://test.atlassian.net/rest/api/3/search/jql?jql=project+%3D+CP")
        assert headers == basic_auth("reader@example.org", "fixture-${LITERAL}")
        return {"isLast": True, "issues": []}

    def page(method, url, *, headers):
        assert method == "GET"
        assert url == "https://test.atlassian.net/wiki/api/v2/pages/123?body-format=storage"
        assert headers == basic_auth("reader@example.org", "page-fixture")
        return {"id": "123", "title": "Example", "version": {"number": 2}, "body": {"storage": {"value": "Example"}}}

    monkeypatch.setattr(jira, "request_json", search)
    monkeypatch.setattr(confluence, "request_json", page)
    assert main(["dev", "backlog"]) == 0
    assert main(["dev", "context", "123"]) == 0
    assert "fixture" not in capsys.readouterr().out
    monkeypatch.setenv("JIRA_API_TOKEN", "process-fixture")
    assert Config.load().secret("jira.token_env") == "process-fixture"
    assert Config.load().get("jira.enabled") is False
