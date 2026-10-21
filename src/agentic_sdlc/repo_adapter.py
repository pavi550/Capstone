"""Detect the repository's stack and the build / test / lint commands to run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .config import Config

PY = sys.executable

STACKS: list[tuple[str, tuple[str, ...], dict[str, list[str] | None]]] = [
    ("maven", ("pom.xml",), {
        "build": ["mvn", "-B", "-q", "compile"], "test": ["mvn", "-B", "-q", "test"],
        "lint": None}),
    ("gradle", ("build.gradle", "build.gradle.kts"), {
        "build": ["gradle", "assemble"], "test": ["gradle", "test"], "lint": None}),
    ("node", ("package.json",), {
        "build": ["npm", "run", "build", "--if-present"], "test": ["npm", "test"],
        "lint": ["npx", "--no-install", "eslint", "."]}),
    ("go", ("go.mod",), {
        "build": ["go", "build", "./..."], "test": ["go", "test", "./..."],
        "lint": ["go", "vet", "./..."]}),
    ("rust", ("Cargo.toml",), {
        "build": ["cargo", "build"], "test": ["cargo", "test"],
        "lint": ["cargo", "clippy", "--", "-D", "warnings"]}),
    ("python", ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"), {
        "build": [PY, "-m", "compileall", "-q", "."],
        "test": [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        "lint": [PY, "-m", "ruff", "check", "."]}),
]


def detect_stack(repo: Path) -> str:
    for name, markers, _ in STACKS:
        if any((repo / m).exists() for m in markers):
            return name
    if any(repo.glob("*.sln")) or any(repo.glob("*.csproj")):
        return "dotnet"
    if any(repo.rglob("*.py")):
        return "python"
    return "unknown"


def commands_for(repo: Path, cfg: Config) -> dict[str, list[str] | str | None]:
    stack = detect_stack(repo)
    defaults: dict[str, list[str] | None] = {"build": None, "test": None, "lint": None}
    for name, _, cmds in STACKS:
        if name == stack:
            defaults = dict(cmds)
    if stack == "dotnet":
        defaults = {"build": ["dotnet", "build"], "test": ["dotnet", "test"], "lint": None}
    if stack == "python" and importlib.util.find_spec("ruff") is None:
        defaults["lint"] = None
    for step in ("build", "test", "lint"):
        override = cfg.get(f"repo.{step}_command")
        if override == "none":
            defaults[step] = None
        elif override:
            defaults[step] = override
    return {"stack": stack, **defaults}
