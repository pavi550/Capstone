"""LLM access: deterministic offline stub (default) and an OpenAI-compatible HTTP client.

Every agent call goes through `LLM.complete(task, system, user, context)` and expects a JSON reply.
`context` carries the structured inputs the stub needs; real models only see `system` + `user`.
"""

from __future__ import annotations

import json
import math
import re
from importlib import resources
from threading import Lock
from time import perf_counter
from typing import Any, Protocol
from urllib.error import URLError

from .config import Config
from .httpclient import HttpError, request_json


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
        self._usage_lock = Lock()
        self._usage = []
        self.api_key = cfg.secret("llm.api_key_env")
        if not self.api_key:
            raise RuntimeError(f"env var {cfg.get('llm.api_key_env')} is not set")

    def complete(self, task: str, system: str, user: str, context: dict | None = None) -> str:
        fallback = self.cfg.get("llm.fallback", [])
        if not isinstance(fallback, list) or len(fallback) > 3:
            raise ValueError("Configure at most three explicit real-model fallback profiles")
        clients = [self]
        if fallback:
            from .development import validate_provider
            from .lifecycle import current_stage

            if current_stage.get() is None:
                raise ValueError("Provider fallback requires a persistent lifecycle stage")
            for profile in fallback:
                if not isinstance(profile, dict) or profile.get("provider") not in {"openai", "anthropic", "gemini"}:
                    raise ValueError("Fallback profiles must select a real model adapter")
                if any(key in profile for key in ("api_key", "fallback")):
                    raise ValueError("Fallback profiles use environment-backed authentication")
                configured = self.cfg.with_overrides({"llm": {**profile, "fallback": []}})
                validate_provider(configured, True)
                clients.append(make_llm(configured))
        for index, client in enumerate(clients):
            try:
                content = client._complete_once(task, system, user)
            except (HttpError, TimeoutError, URLError) as exc:
                retryable = (isinstance(exc, HttpError) and exc.status in {429, 502, 503, 504}) or isinstance(
                    getattr(exc, "reason", exc), TimeoutError,
                )
                if not retryable or index + 1 == len(clients):
                    raise
            else:
                if client is not self:
                    with self._usage_lock:
                        self._usage.extend(client.usage())
                return content
        raise RuntimeError("Provider route exhausted")

    def _complete_once(self, task, system, user):
        limit = self.cfg.get("llm.max_tokens")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 32768):
            raise ValueError("llm.max_tokens must be between 1 and 32768")
        input_limit = self.cfg.get("llm.max_input_bytes", 200000)
        if type(input_limit) is not int or not 1 <= input_limit <= 1000000:
            raise ValueError("llm.max_input_bytes must be between 1 and 1000000")
        if len(system.encode()) + len(user.encode()) > input_limit:
            raise ValueError("Model input exceeds the configured byte budget")
        rates = (self.cfg.get("llm.input_cost_per_million"), self.cfg.get("llm.output_cost_per_million"))
        if any(rate is not None and (type(rate) not in {int, float} or not math.isfinite(rate) or rate < 0)
               for rate in rates):
            raise ValueError("Model pricing must use nonnegative finite rates")
        from .lifecycle import checkpoint, current_stage
        from .provider_budget import normalize_usage, reconcile, reserve

        checkpoint()
        reservation = reserve(current_stage.get(), self.cfg, task, len(system.encode()) + len(user.encode()) + 256,
                              limit, rates)
        started = perf_counter()
        try:
            reply, content, _input_count, _output_count = self._request(system, user, limit)
        except BaseException:
            reconcile(reservation)
            raise
        duration_ms = round((perf_counter() - started) * 1000, 3)
        normalized = normalize_usage(self.name, reply, self.cfg)
        inputs, outputs = normalized["input_tokens"], normalized["output_tokens"]
        priced = all(value is not None for value in (*rates, inputs, outputs))
        cost = (inputs * rates[0] + outputs * rates[1]) / 1_000_000 if priced else None
        reconcile(reservation, inputs, outputs, cost if self.cfg.get("llm.currency", "USD") == "USD" else None,
                  status="RECONCILED" if inputs is not None and outputs is not None else "USAGE_UNKNOWN", usage=normalized)
        checkpoint()
        recorded_model = reply.get("model")
        with self._usage_lock:
            self._usage.append({
                **normalized,
                "task": task, "request_id": reservation[1] if reservation else None, "provider": self.name, "configured_model": self.cfg.get("llm.model"),
                "reported_model": recorded_model if isinstance(recorded_model, str) else None,
                "input_tokens": inputs,
                "output_tokens": outputs,
                "duration_ms": duration_ms,
                "cost": cost,
                "currency": self.cfg.get("llm.currency", "USD") if priced else None,
            })
        return content

    def _request(self, system, user, limit):
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
        if limit is not None:
            body["max_tokens"] = limit
        reply = request_json(
            "POST", url, headers={header: value}, body=body, timeout=self.cfg.get("llm.timeout", 120),
        )
        usage = reply.get("usage") or {}
        return reply, reply["choices"][0]["message"]["content"], usage.get("prompt_tokens"), usage.get("completion_tokens")

    def usage(self) -> list[dict]:
        with self._usage_lock:
            return [record.copy() for record in sorted(self._usage, key=lambda row: row["task"])]


class GeminiLLM(OpenAICompatibleLLM):
    """Use Google's OpenAI-compatible Gemini endpoint."""

    name = "gemini"


class AnthropicLLM(OpenAICompatibleLLM):
    name = "anthropic"

    def _request(self, system, user, limit):
        if limit is None:
            raise ValueError("Anthropic requires llm.max_tokens")
        body = {"model": self.cfg.get("llm.model"), "max_tokens": limit,
                "system": system + " Return only the requested JSON object.",
                "messages": [{"role": "user", "content": user}]}
        result = request_json(
            "POST", self.cfg.get("llm.base_url").rstrip("/") + "/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            body=body, timeout=self.cfg.get("llm.timeout", 120),
        )
        content = "".join(block["text"] for block in result["content"] if block["type"] == "text")
        usage = result.get("usage") or {}
        return result, content, usage.get("input_tokens"), usage.get("output_tokens")


def _usage_count(value):
    return value if type(value) is int and value >= 0 else None


def make_llm(cfg: Config) -> LLM:
    provider = cfg.get("llm.provider", "stub")
    if provider == "stub":
        return StubLLM()
    adapter = {"openai": OpenAICompatibleLLM, "gemini": GeminiLLM, "anthropic": AnthropicLLM}.get(provider)
    if adapter is None:
        raise ValueError(f"unknown llm.provider: {provider}")
    return adapter(cfg)
