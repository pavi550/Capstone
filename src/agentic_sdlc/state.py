"""Data model shared by every stage of the flow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Status(str, Enum):
    PENDING = "pending"
    TESTED = "tested"
    BLOCKED = "blocked"
    APPROVED = "approved"
    REVIEW_FAILED = "review_failed"


@dataclass
class Requirement:
    id: str
    title: str
    description: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"{self.id}: {self.title}", self.description]
        if self.acceptance_criteria:
            parts.append("Acceptance criteria:\n" + "\n".join(f"- {c}" for c in self.acceptance_criteria))
        if self.notes:
            parts.append("Clarifications:\n" + "\n".join(f"- {n}" for n in self.notes))
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Requirement:
        return cls(
            id=str(data["id"]),
            title=str(data.get("title", data["id"])),
            description=str(data.get("description", "")),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            source=str(data.get("source", "")),
            notes=list(data.get("notes", [])),
        )


@dataclass
class Finding:
    reviewer: str
    category: str
    severity: str
    message: str
    file: str = ""
    line: int = 0
    rule: str = ""

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.file, self.line, self.rule or self.category)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileChange:
    path: str
    content: str


@dataclass
class CheckResult:
    passed: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    output: str = ""
    coverage: float | None = None


@dataclass
class Work:
    """Per-requirement progress through the flow."""

    requirement: Requirement
    status: Status = Status.PENDING
    files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    last_check: CheckResult | None = None
    feedback: str | None = None
    feedback_kind: str | None = None  # test | code | requirement | review | regression
    pending_violations: list[str] = field(default_factory=list)
    fix_iterations: int = 0
    review_iterations: int = 0
    triage_history: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    findings: dict[str, list[Finding]] = field(default_factory=dict)
    consensus: list[Finding] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    reviewer_metrics: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class RunState:
    run_id: str
    out_dir: Path
    repo_path: Path | None
    requirement_sources: list[Path] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    work: dict[str, Work] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    phase: str = "build"
    current: str | None = None
    build_queue: list[str] = field(default_factory=list)
    review_queue: list[str] = field(default_factory=list)
    ci_iterations: int = 0
    concern: str | None = None
    history: list[str] = field(default_factory=list)
    vcs_result: dict[str, Any] = field(default_factory=dict)
    ci_result: CheckResult | None = None
    deployments: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)

    @property
    def cur(self) -> Work:
        if self.current is None:
            raise RuntimeError("no current requirement selected")
        return self.work[self.current]

    def by_status(self, *statuses: Status) -> list[Work]:
        return [w for w in self.work.values() if w.status in statuses]
