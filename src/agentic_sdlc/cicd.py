"""10. CI/CD pipeline: configured `ci.command` (Jenkins, GitLab CI, Zuul, ...) or the full
build + lint + regression test suite of the repository."""

from __future__ import annotations

from pathlib import Path

from . import shell
from .config import Config
from .quality import run_checks
from .state import CheckResult


def run_pipeline(repo: Path, cfg: Config, commands: dict) -> CheckResult:
    cmd = cfg.get("ci.command")
    if cmd:
        res = shell.run(cmd, cwd=repo)
        return CheckResult(passed=res.ok, output=res.output,
                           steps=[{"step": "ci", "cmd": res.cmd, "returncode": res.returncode}])
    return run_checks(repo, commands, cfg.get("quality.min_coverage"))
