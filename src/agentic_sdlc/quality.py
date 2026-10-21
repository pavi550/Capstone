"""Step 6: test execution & quality (build, lint, test, coverage) and failure triage."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import shell
from .llm import LLM, load_prompt, parse_json
from .state import CheckResult, Work

_COVERAGE = re.compile(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%\s*$", re.MULTILINE)
TRIAGE_CATEGORIES = ("test", "code", "requirement")


def run_checks(
    repo: Path, commands: dict, min_coverage: float | None = None, steps=("build", "lint", "test")
) -> CheckResult:
    results, outputs, passed, coverage = [], [], True, None
    for step in steps:
        cmd = commands.get(step)
        if not cmd:
            continue
        res = shell.run(cmd, cwd=repo)
        results.append({"step": step, "cmd": res.cmd, "returncode": res.returncode})
        outputs.append(f"$ {' '.join(res.cmd)}\n{res.output}")
        passed &= res.ok
        if step == "test":
            m = _COVERAGE.search(res.output)
            coverage = float(m.group(1)) if m else None
    if min_coverage is not None and coverage is not None and coverage < min_coverage:
        passed = False
        outputs.append(f"coverage {coverage}% is below the {min_coverage}% gate")
    return CheckResult(passed=passed, steps=results, output="\n\n".join(outputs), coverage=coverage)


def triage(llm: LLM, work: Work, check: CheckResult) -> tuple[str, str]:
    """Decide whether the test, the code or the requirement is incorrect."""
    req = work.requirement
    user = json.dumps({
        "requirement": req.text(), "code_files": work.files, "test_files": work.test_files,
        "output": check.output[-6000:],
    })
    reply = parse_json(llm.complete(
        "triage", load_prompt("FailureTriage.md"), user,
        {"requirement": req.to_dict(), "output": check.output},
    ))
    category = reply.get("category", "code")
    return (category if category in TRIAGE_CATEGORIES else "code"), reply.get("reason", "")
