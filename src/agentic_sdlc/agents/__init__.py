"""Agents (5a coding, 5b test, 7 review) and the guarded file-write helper they share."""

from __future__ import annotations

import json
from pathlib import Path

from ..guardrails import Guardrails, Violation
from ..knowledge_base import KnowledgeBase
from ..llm import LLM, load_prompt, parse_json
from ..state import Work

SKIP_DIRS = {".git", ".hg", "node_modules", "__pycache__", ".venv", "venv", "target", "dist",
             "build", ".agentic_sdlc", ".pytest_cache", ".ruff_cache"}


def repo_tree(repo: Path, limit: int = 300) -> list[str]:
    out = []
    for p in sorted(repo.rglob("*")):
        if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(repo).parts):
            out.append(p.relative_to(repo).as_posix())
            if len(out) >= limit:
                break
    return out


def read_files(repo: Path, paths: list[str], max_chars: int = 20000) -> dict[str, str]:
    out = {}
    for rel in paths:
        p = repo / rel
        if p.is_file():
            out[rel] = p.read_text(encoding="utf-8", errors="replace")[:max_chars]
    return out


def apply_changes(
    repo: Path, files: list[dict], guardrails: Guardrails, tests_only: bool = False
) -> tuple[list[str], list[Violation]]:
    """Write files that pass every guardrail; return (written paths, violations)."""
    written, violations = [], []
    for item in files:
        rel, content = str(item["path"]).replace("\\", "/"), str(item.get("content", ""))
        problems = guardrails.check_write(repo, rel, content, tests_only=tests_only)
        if problems:
            violations.extend(problems)
            continue
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written, violations


class GeneratingAgent:
    """Prompts the LLM with requirement + RAG context and applies the returned files."""

    prompt = ""
    task = ""
    tests_only = False

    def __init__(self, llm: LLM, guardrails: Guardrails, kb: KnowledgeBase, top_k: int = 3):
        self.llm = llm
        self.guardrails = guardrails
        self.kb = kb
        self.top_k = top_k

    def generate(
        self, work: Work, repo: Path, own_files: list[str], feedback: str | None, task: str
    ) -> tuple[list[str], list[Violation]]:
        req = work.requirement
        related = self.kb.retrieve(req.text(), self.top_k, exclude=req.id)
        user = json.dumps({
            "requirement": req.text(),
            "related_requirements": [r.text() for r in related],
            "repository_files": repo_tree(repo),
            "current_files": read_files(repo, own_files),
            "feedback": feedback,
        })
        reply = parse_json(self.llm.complete(
            task, load_prompt(self.prompt), user,
            {"requirement": req.to_dict(), "feedback": feedback},
        ))
        written, violations = apply_changes(
            repo, reply.get("files", []), self.guardrails, tests_only=self.tests_only
        )
        work.violations.extend(str(v) for v in violations)
        return written, violations
