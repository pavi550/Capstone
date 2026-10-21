"""5b. Test agent: writes (or corrects) tests for one requirement. May only write test files."""

from __future__ import annotations

from pathlib import Path

from ..state import Work
from . import GeneratingAgent


class TestAgent(GeneratingAgent):
    __test__ = False
    prompt = "TestAgent.md"
    tests_only = True

    def write_tests(self, work: Work, repo: Path, feedback: str | None = None):
        written, violations = self.generate(
            work, repo, work.test_files + work.files, feedback, "test"
        )
        work.test_files = sorted(set(work.test_files) | set(written))
        return written, violations
