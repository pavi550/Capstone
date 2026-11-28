from conftest import make_cfg

from agentic_sdlc.agents import apply_changes
from agentic_sdlc.guardrails import Guardrails

FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


def rules(violations):
    return {v.rule for v in violations}


def test_blocks_hard_coded_secret(tmp_path):
    g = Guardrails(make_cfg())
    assert "secret" in rules(g.check_write(tmp_path, "app.py", f'KEY = "{FAKE_AWS_KEY}"\n'))
    assert "secret" in rules(g.check_write(tmp_path, "app.py", 'password = "hunter2hunter2"\n'))
    assert not g.check_write(tmp_path, "app.py", "x = 1\n")


def test_blocks_restricted_paths_and_traversal(tmp_path):
    g = Guardrails(make_cfg())
    assert "restricted_path" in rules(g.check_write(tmp_path, ".github/workflows/ci.yml", ""))
    assert "restricted_path" in rules(g.check_write(tmp_path, "config/.env", ""))
    assert "restricted_path" in rules(g.check_write(tmp_path, ".git/config", ""))
    assert "path_traversal" in rules(g.check_write(tmp_path, "../outside.py", ""))


def test_test_agent_may_only_write_tests(tmp_path):
    g = Guardrails(make_cfg())
    assert "tests_only" in rules(g.check_write(tmp_path, "app.py", "", tests_only=True))
    assert not g.check_write(tmp_path, "tests/test_app.py", "", tests_only=True)
    assert not g.check_write(tmp_path, "src/app.test.js", "", tests_only=True)


def test_file_size_limit(tmp_path):
    g = Guardrails(make_cfg(guardrails={"max_file_bytes": 10}))
    assert "file_size" in rules(g.check_write(tmp_path, "a.py", "x" * 11))


def test_apply_changes_writes_only_allowed_files(tmp_path):
    g = Guardrails(make_cfg())
    written, violations = apply_changes(tmp_path, [
        {"path": "ok.py", "content": "x = 1\n"},
        {"path": ".env", "content": "A=1"},
        {"path": "leak.py", "content": f'K = "{FAKE_AWS_KEY}"'},
    ], g)
    assert written == ["ok.py"] and len(violations) == 2
    assert not (tmp_path / ".env").exists() and not (tmp_path / "leak.py").exists()


def test_repo_and_mcp_checks(tmp_path):
    g = Guardrails(make_cfg())
    assert rules(g.check_repo(None)) == {"repo_access"}
    assert rules(g.check_repo(tmp_path / "missing")) == {"repo_access"}
    assert not g.check_repo(tmp_path)
    assert not g.check_mcp("gerrit")
    assert rules(g.check_mcp("slack")) == {"mcp_approval"}
