"""LLM access: deterministic offline stub (default) and an OpenAI-compatible HTTP client.

Every agent call goes through `LLM.complete(task, system, user, context)` and expects a JSON reply.
`context` carries the structured inputs the stub needs; real models only see `system` + `user`.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any, Protocol

from .config import Config
from .httpclient import request_json


class LLM(Protocol):
    name: str

    def complete(self, task: str, system: str, user: str, context: dict | None = None) -> str: ...


def load_prompt(name: str) -> str:
    return resources.files("agentic_sdlc").joinpath("prompts", name).read_text(encoding="utf-8")


def parse_json(text: str) -> Any:
    """Parse a JSON reply, tolerating ```json fences and leading prose."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"no JSON object in LLM reply: {text[:200]!r}")
    return json.loads(text[start:])


def module_name(req_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", req_id.lower()).strip("_") or "req"


class StubLLM:
    """Offline, deterministic stand-in. Produces scaffold code/tests so the full flow runs."""

    name = "stub"

    def complete(self, task: str, system: str, user: str, context: dict | None = None) -> str:
        ctx = context or {}
        handler = getattr(self, f"_{task}", None)
        return json.dumps(handler(ctx) if handler else {})

    def _plan(self, ctx: dict) -> dict:
        return {
            "tasks": [
                {
                    "requirement": r["id"],
                    "coding": f"Implement {r['title']}",
                    "testing": f"Write tests for {len(r.get('acceptance_criteria', []))} criteria",
                }
                for r in ctx.get("requirements", [])
            ]
        }

    def _code(self, ctx: dict) -> dict:
        req = ctx["requirement"]
        mod = module_name(req["id"])
        criteria = "".join(f"    {c!r},\n" for c in req.get("acceptance_criteria", []))
        body = (
            f'"""{req["id"]}: {req["title"]}\n\n'
            "Generated scaffold (offline stub LLM). Replace with a real implementation.\n"
            '"""\n\n'
            f"REQUIREMENT_ID = {req['id']!r}\n"
            f"TITLE = {req['title']!r}\n"
            f"ACCEPTANCE_CRITERIA = [\n{criteria}]\n\n\n"
            "def describe() -> str:\n"
            '    return f"{REQUIREMENT_ID}: {TITLE}"\n'
        )
        return {
            "summary": f"scaffold for {req['id']}",
            "files": [
                {"path": "agentic_impl/__init__.py", "content": ""},
                {"path": f"agentic_impl/{mod}.py", "content": body},
            ],
        }

    _fix_review = _code

    def _test(self, ctx: dict) -> dict:
        req = ctx["requirement"]
        mod = module_name(req["id"])
        count = len(req.get("acceptance_criteria", []))
        body = (
            f'"""Tests for {req["id"]}: {req["title"]}"""\n\n'
            f"from agentic_impl import {mod} as impl\n\n\n"
            f"def test_{mod}_describe():\n"
            f"    assert impl.describe() == {req['id'] + ': ' + req['title']!r}\n\n\n"
            f"def test_{mod}_acceptance_criteria_recorded():\n"
            f"    assert len(impl.ACCEPTANCE_CRITERIA) == {count}\n"
        )
        return {"summary": f"tests for {req['id']}", "files": [
            {"path": f"tests/test_{mod}.py", "content": body}
        ]}

    def _triage(self, ctx: dict) -> dict:
        out = ctx.get("output", "")
        if re.search(r"requirement (is )?(ambiguous|unclear|contradict)", out, re.IGNORECASE):
            return {"category": "requirement", "reason": "requirement flagged as ambiguous"}
        if re.search(r"(fixture .* not found|tests?[/\\][^\s]*\.py.*(SyntaxError|NameError))", out):
            return {"category": "test", "reason": "failure originates in the test file"}
        return {"category": "code", "reason": "default: production code does not satisfy tests"}

    def _review(self, ctx: dict) -> dict:
        return {"findings": []}


class OpenAICompatibleLLM:
    """Chat-completions client for OpenAI, Azure OpenAI, Ollama, vLLM, LM Studio, ..."""

    name = "openai"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.api_key = cfg.secret("llm.api_key_env")
        if not self.api_key:
            raise RuntimeError(f"env var {cfg.get('llm.api_key_env')} is not set")

    def complete(self, task: str, system: str, user: str, context: dict | None = None) -> str:
        url = self.cfg.get("llm.base_url").rstrip("/") + "/chat/completions"
        if self.cfg.get("llm.api_version"):
            url += f"?api-version={self.cfg.get('llm.api_version')}"
        header = self.cfg.get("llm.api_key_header", "Authorization")
        value = f"Bearer {self.api_key}" if header == "Authorization" else self.api_key
        body = {
            "model": self.cfg.get("llm.model"),
            "temperature": self.cfg.get("llm.temperature", 0.0),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        reply = request_json(
            "POST", url, headers={header: value}, body=body, timeout=self.cfg.get("llm.timeout", 120)
        )
        return reply["choices"][0]["message"]["content"]


def make_llm(cfg: Config) -> LLM:
    provider = cfg.get("llm.provider", "stub")
    if provider == "stub":
        return StubLLM()
    if provider == "openai":
        return OpenAICompatibleLLM(cfg)
    raise ValueError(f"unknown llm.provider: {provider}")
