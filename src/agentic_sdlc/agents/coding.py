"""5a. Coding agent: implements one requirement, or refines it from test/review/regression feedback."""

from __future__ import annotations

from pathlib import Path

from ..state import Work
from . import GeneratingAgent


class CodingAgent(GeneratingAgent):
    prompt = "CodingAgent.md"

    def implement(self, work: Work, repo: Path, feedback: str | None = None, task: str = "code"):
        written, violations = self.generate(work, repo, work.files, feedback, task)
        work.files = sorted(set(work.files) | set(written))
        return written, violations
