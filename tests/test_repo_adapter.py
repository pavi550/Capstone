import pytest
from conftest import make_cfg

from agentic_sdlc.repo_adapter import commands_for, detect_stack


@pytest.mark.parametrize("marker,stack", [
    ("pom.xml", "maven"), ("package.json", "node"), ("go.mod", "go"),
    ("Cargo.toml", "rust"), ("pyproject.toml", "python"), ("app.csproj", "dotnet"),
])
def test_detect_stack(tmp_path, marker, stack):
    (tmp_path / marker).write_text("", encoding="utf-8")
    assert detect_stack(tmp_path) == stack


def test_unknown_stack(tmp_path):
    assert detect_stack(tmp_path) == "unknown"


def test_overrides_and_disable(tmp_path):
    (tmp_path / "go.mod").write_text("", encoding="utf-8")
    cmds = commands_for(tmp_path, make_cfg(repo={"test_command": "make test",
                                                 "build_command": "none"}))
    assert cmds["stack"] == "go" and cmds["test"] == "make test" and cmds["build"] is None
