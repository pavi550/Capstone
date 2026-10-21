import json
import shutil
from pathlib import Path

import pytest

from agentic_sdlc.config import Config
from agentic_sdlc.llm import StubLLM, module_name
from agentic_sdlc.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(EXAMPLES / "sample_repo", target)
    return target


def make_cfg(**overrides) -> Config:
    base = {
        "repo": {"build_command": "none", "lint_command": "none"},
        "review": {"external_tools": "none"},
    }
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return Config.load(overrides=base)


@pytest.fixture
def cfg():
    return make_cfg()


def write_requirements(tmp_path: Path, count: int = 1) -> Path:
    blocks = [
        f"## REQ-{i:03d}: Feature {i}\n\nAcceptance criteria:\n- feature {i} works\n"
        for i in range(1, count + 1)
    ]
    path = tmp_path / "requirements.md"
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def code_reply(req: dict, describe: str | None = None, extra: str = "") -> dict:
    mod = module_name(req["id"])
    value = describe if describe is not None else f"{req['id']}: {req['title']}"
    criteria = "".join(f"    {c!r},\n" for c in req.get("acceptance_criteria", []))
    body = (f'"""{req["id"]}"""\n{extra}\nACCEPTANCE_CRITERIA = [\n{criteria}]\n\n\n'
            f"def describe() -> str:\n    return {value!r}\n")
    return {"files": [{"path": "agentic_impl/__init__.py", "content": ""},
                      {"path": f"agentic_impl/{mod}.py", "content": body}]}


class ScriptedLLM(StubLLM):
    """Stub LLM whose replies can be overridden per task, in order, then falls back to the stub."""

    def __init__(self, **script):
        self.script = {task: list(replies) for task, replies in script.items()}
        self.calls: list[str] = []

    def complete(self, task, system, user, context=None):
        self.calls.append(task)
        queue = self.script.get(task)
        if queue:
            reply = queue.pop(0)
            return json.dumps(reply(context or {}) if callable(reply) else reply)
        return super().complete(task, system, user, context)


def run_flow(tmp_path, repo, cfg, llm=None, count=1, **kwargs):
    reqs = write_requirements(tmp_path, count)
    orch = Orchestrator(cfg, llm=llm or StubLLM(), **kwargs)
    return orch, orch.run([reqs], repo, tmp_path / "runs")
