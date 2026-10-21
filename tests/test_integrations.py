"""Live clients (Jira REST, Gerrit, ChromaDB, OpenAI-compatible LLM) with the network mocked."""

import json
import shutil
import subprocess
import sys
import types

import pytest
from conftest import make_cfg, run_flow

from agentic_sdlc import httpclient, jira, llm, vcs
from agentic_sdlc.events import EventLog
from agentic_sdlc.guardrails import Guardrails
from agentic_sdlc.hitl import HITL
from agentic_sdlc.knowledge_base import ChromaStore, make_store
from agentic_sdlc.orchestrator import END
from agentic_sdlc.state import Requirement, Work


@pytest.fixture
def http(monkeypatch):
    calls, replies = [], []

    def fake(method, url, *, headers=None, body=None, timeout=60):
        calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        return replies.pop(0)

    for module in (jira, vcs, llm):
        monkeypatch.setattr(module, "request_json", fake)
    return calls, replies


def test_http_helper_rejects_non_http_urls():
    with pytest.raises(ValueError):
        httpclient.request_json("GET", "file:///etc/passwd")


def test_jira_search_and_create(monkeypatch, http):
    calls, replies = http
    monkeypatch.setenv("JIRA_USER", "bot")
    monkeypatch.setenv("JIRA_TOKEN", "t0ken")
    cfg = make_cfg(jira={"enabled": True, "base_url": "https://jira.example.com",
                         "jql": "project = CAP"})
    replies.append({"issues": [{"key": "CAP-1", "fields": {
        "summary": "Login", "description": "Acceptance criteria:\n- login works"}}]})
    [req] = jira.fetch_requirements(cfg)
    assert (req.id, req.acceptance_criteria, req.source) == ("CAP-1", ["login works"], "jira")
    assert calls[0]["url"].startswith("https://jira.example.com/rest/api/2/search?jql=project")
    assert calls[0]["headers"]["Authorization"].startswith("Basic ")

    replies.append({"key": "CAP-2"})
    assert jira.JiraClient(cfg).create_issue("CAP", "Regression", "log", "Bug") == "CAP-2"
    assert calls[1]["body"]["fields"]["issuetype"] == {"name": "Bug"}


def test_jira_requires_credentials(monkeypatch):
    monkeypatch.delenv("JIRA_USER", raising=False)
    with pytest.raises(RuntimeError):
        jira.JiraClient(make_cfg(jira={"base_url": "https://jira.example.com"}))


def test_openai_compatible_llm(monkeypatch, http):
    calls, replies = http
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = make_cfg(llm={"provider": "openai", "base_url": "http://localhost:11434/v1",
                        "model": "llama3"})
    replies.append({"choices": [{"message": {"content": '{"tasks": []}'}}]})
    client = llm.make_llm(cfg)
    assert client.complete("plan", "sys", "user") == '{"tasks": []}'
    assert calls[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert calls[0]["headers"] == {"Authorization": "Bearer sk-test"}
    assert calls[0]["body"]["model"] == "llama3"


def test_azure_style_llm_header(monkeypatch, http):
    calls, replies = http
    monkeypatch.setenv("OPENAI_API_KEY", "az")
    cfg = make_cfg(llm={"provider": "openai", "base_url": "https://x.openai.azure.com/dep",
                        "api_key_header": "api-key", "api_version": "2024-06-01"})
    replies.append({"choices": [{"message": {"content": "{}"}}]})
    llm.make_llm(cfg).complete("plan", "s", "u")
    assert calls[0]["url"].endswith("/chat/completions?api-version=2024-06-01")
    assert calls[0]["headers"] == {"api-key": "az"}


def test_chroma_store_with_fake_client(monkeypatch, tmp_path):
    store: dict = {}

    class Collection:
        def upsert(self, ids, documents):
            store.update(zip(ids, documents))

        def query(self, query_texts, n_results):
            ids = list(store)[:n_results]
            return {"ids": [ids], "distances": [[0.1] * len(ids)]}

    fake = types.ModuleType("chromadb")
    fake.PersistentClient = lambda path: types.SimpleNamespace(
        get_or_create_collection=lambda name, **kw: Collection())
    monkeypatch.setitem(sys.modules, "chromadb", fake)
    s = make_store(make_cfg(knowledge_base={"store": "chroma"}))
    assert isinstance(s, ChromaStore)
    s.upsert("REQ-1", "login")
    assert s.query("login", 3) == [("REQ-1", 0.9)]


def _git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def git_repo(tmp_path, repo):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "bot@example.com")
    _git(repo, "config", "user.name", "bot")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


class FakeGerrit:
    def __init__(self):
        self.calls = []

    def get_change(self, change_id):
        self.calls.append(("get", change_id))
        return {"project": "calc", "_number": 42, "status": "NEW"}

    def change_url(self, change):
        return "https://gerrit.example.com/c/calc/+/42"

    def review(self, change_id, labels, message=""):
        self.calls.append(("review", labels))

    def submit(self, change_id):
        self.calls.append(("submit", change_id))


def test_gerrit_push_review_and_submit(tmp_path, git_repo):
    repo, remote = git_repo
    cfg = make_cfg(vcs={"mode": "gerrit", "gerrit": {"auto_submit": True}})
    events = EventLog(tmp_path / "e.jsonl", "r1")
    gerrit = FakeGerrit()
    v = vcs.VCS(cfg, events, HITL(cfg), Guardrails(cfg), gerrit=gerrit)
    (repo / "feature.py").write_text('"""REQ-1"""\n', encoding="utf-8")
    work = Work(Requirement("REQ-1", "Feature"), files=["feature.py"])
    result = v.integrate(repo, [work], "r1")
    assert result["ok"], result
    commit = result["commits"][0]
    msg = _git(repo, "log", "-1", "--format=%B")
    assert msg.startswith("REQ-1: Feature") and f"Change-Id: {commit['change_id']}" in msg
    assert "refs/for/main" in _git(remote, "for-each-ref", "--format=%(refname)")
    assert ("review", {"Code-Review": 2}) in gerrit.calls
    assert result["reviews"][0]["status"] == "MERGED"


def test_gerrit_waits_for_human_without_approval(tmp_path, git_repo):
    repo, _ = git_repo
    cfg = make_cfg(vcs={"mode": "gerrit"}, hitl={"auto_approve": []})
    v = vcs.VCS(cfg, EventLog(tmp_path / "e.jsonl", "r"), HITL(cfg), Guardrails(cfg),
                gerrit=FakeGerrit())
    (repo / "f.py").write_text("x = 1\n", encoding="utf-8")
    result = v.integrate(repo, [Work(Requirement("REQ-2", "F"), files=["f.py"])], "r")
    assert result["reviews"][0]["status"] == "awaiting human Code-Review +2"


def test_gerrit_mode_requires_mcp_approval(tmp_path, repo):
    cfg = make_cfg(vcs={"mode": "gerrit"}, guardrails={"allowed_mcp": []})
    _, state = run_flow(tmp_path, repo, cfg)
    assert state.history[-3:] == ["guardrails", "concern", "halt"]
    assert "gerrit" in state.concern


def test_full_flow_in_git_mode_commits_requirement(tmp_path, git_repo):
    repo, _ = git_repo
    _, state = run_flow(tmp_path, repo, make_cfg(vcs={"mode": "git"}))
    assert state.history[-1] == END and state.vcs_result["ok"]
    assert _git(repo, "log", "--format=%s").splitlines()[0] == "REQ-001: Feature 1"
    assert json.loads((state.out_dir / "review-REQ-001.json").read_text())["passed"]
