import json
import zipfile

from conftest import EXAMPLES

from agentic_sdlc.jira import split_description
from agentic_sdlc.knowledge_base import KeywordStore, KnowledgeBase, parse_requirements
from agentic_sdlc.state import Requirement


def test_parse_markdown():
    reqs = parse_requirements(EXAMPLES / "requirements.md")
    assert [r.id for r in reqs] == ["REQ-001", "REQ-002"]
    assert reqs[0].title == "Subtract two numbers"
    assert reqs[0].acceptance_criteria == [
        "subtract returns the difference of two numbers", "subtract supports negative results"]
    assert "subtraction" in reqs[0].description


def test_parse_markdown_without_ids_generates_them(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("# Title\n\n## Login\nUsers log in.\n\n## Logout\n", encoding="utf-8")
    assert [(r.id, r.title) for r in parse_requirements(p)] == [
        ("REQ-001", "Login"), ("REQ-002", "Logout")]


def test_parse_html(tmp_path):
    p = tmp_path / "r.html"
    p.write_text("<h1>Space</h1><h2>REQ-7: Search</h2><p>Find items.</p>"
                 "<p>Acceptance criteria:</p><ul><li>search by name</li></ul>", encoding="utf-8")
    [req] = parse_requirements(p)
    assert (req.id, req.title, req.acceptance_criteria) == ("REQ-7", "Search", ["search by name"])


def test_parse_json(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"requirements": [{"title": "A", "acceptance_criteria": ["a"]}]}))
    [req] = parse_requirements(p)
    assert (req.id, req.title) == ("REQ-001", "A")


def test_parse_docx(tmp_path):
    p = tmp_path / "r.docx"
    paras = ["REQ-9: Export", "Export reports.", "Acceptance criteria:", "- export to csv"]
    xml = "<w:document><w:body>" + "".join(
        f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paras) + "</w:body></w:document>"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("word/document.xml", xml)
    [req] = parse_requirements(p)
    assert (req.id, req.acceptance_criteria) == ("REQ-9", ["export to csv"])


def test_split_description():
    desc, ac = split_description("Intro\nAcceptance Criteria:\n* one\n# two\n1. three")
    assert desc == "Intro" and ac == ["one", "two", "three"]


def test_knowledge_base_retrieval_and_persistence(tmp_path):
    kb = KnowledgeBase(KeywordStore(), tmp_path / "kb.json")
    kb.upsert(Requirement("A", "login with password"))
    kb.upsert(Requirement("B", "export csv report"))
    kb.upsert(Requirement("C", "password reset email"))
    assert [r.id for r in kb.retrieve("password", k=2)] == ["A", "C"]
    assert [r.id for r in kb.retrieve("password", k=2, exclude="A")] == ["C"]
    kb.save()
    assert len(json.loads((tmp_path / "kb.json").read_text())["requirements"]) == 3
