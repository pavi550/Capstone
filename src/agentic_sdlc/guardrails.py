"""Step 4: guardrails — repo access, restricted paths, secrets, size limits, MCP approvals.

Every file an agent wants to write passes through `check_write` before it touches disk.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Config


@dataclass
class Violation:
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.path}: {self.message}"


class Guardrails:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.restricted = cfg.get("guardrails.restricted_paths", [])
        self.test_paths = cfg.get("guardrails.test_paths", [])
        self.max_bytes = cfg.get("guardrails.max_file_bytes", 200000)
        self.allowed_mcp = set(cfg.get("guardrails.allowed_mcp", []))
        self.patterns = [re.compile(p) for p in cfg.get("guardrails.secret_patterns", [])]

    def check_repo(self, repo: Path | None) -> list[Violation]:
        if repo is None:
            return [Violation("repo_access", "", "no repository path given")]
        if not repo.is_dir():
            return [Violation("repo_access", str(repo), "repository path does not exist")]
        if not os.access(repo, os.R_OK | os.W_OK):
            return [Violation("repo_access", str(repo), "repository is not readable and writable")]
        return []

    def check_mcp(self, name: str) -> list[Violation]:
        if name in self.allowed_mcp:
            return []
        return [Violation("mcp_approval", name, f"MCP/integration '{name}' is not approved")]

    def is_test_path(self, rel: str) -> bool:
        p = PurePosixPath(rel.replace("\\", "/"))
        return any(str(p).startswith(t) for t in self.test_paths) or p.name.startswith("test_") \
            or p.stem.endswith(("_test", ".test", ".spec")) or p.stem.endswith("Test")

    def _restricted(self, rel: str) -> str | None:
        name = PurePosixPath(rel).name
        for rule in self.restricted:
            stem = rule.rstrip("/")
            if rel == stem or rel.startswith(stem + "/") or name == stem:
                return rule
        return None

    def scan_secrets(self, text: str) -> list[tuple[int, str]]:
        hits = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in self.patterns:
                if pat.search(line):
                    hits.append((lineno, pat.pattern))
                    break
        return hits

    def check_write(
        self, repo: Path, rel: str, content: str, tests_only: bool = False
    ) -> list[Violation]:
        rel = rel.replace("\\", "/")
        out: list[Violation] = []
        target = (repo / rel).resolve()
        if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts \
                or not target.is_relative_to(repo.resolve()):
            return [Violation("path_traversal", rel, "path escapes the repository")]
        rule = self._restricted(rel)
        if rule:
            out.append(Violation("restricted_path", rel, f"matches restricted path '{rule}'"))
        if tests_only and not self.is_test_path(rel):
            out.append(Violation("tests_only", rel, "test agent may only write test files"))
        if len(content.encode("utf-8")) > self.max_bytes:
            out.append(Violation("file_size", rel, f"exceeds {self.max_bytes} bytes"))
        for lineno, pattern in self.scan_secrets(content):
            out.append(Violation("secret", f"{rel}:{lineno}", f"matches secret pattern {pattern}"))
        return out
