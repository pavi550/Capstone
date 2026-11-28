"""7c. Ensemble review: voters' findings are grouped and kept only on weighted consensus."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .state import Finding

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
LINE_TOLERANCE = 3


@dataclass
class Group:
    findings: dict[str, Finding] = field(default_factory=dict)  # voter -> finding
    kept: bool = False

    @property
    def representative(self) -> Finding:
        return max(self.findings.values(), key=lambda f: SEVERITY_ORDER.index(f.severity)
                   if f.severity in SEVERITY_ORDER else 0)

    def to_dict(self) -> dict:
        return {"voters": sorted(self.findings), "kept": self.kept,
                "finding": self.representative.to_dict()}


def _same(a: Finding, b: Finding) -> bool:
    if a.file != b.file or a.category != b.category:
        return False
    return a.line == 0 or b.line == 0 or abs(a.line - b.line) <= LINE_TOLERANCE


class Ensemble:
    def __init__(self, cfg: Config):
        self.weights: dict[str, float] = cfg.get("review.ensemble.weights", {})
        self.threshold: float = cfg.get("review.ensemble.threshold", 0.5)
        self.always_keep = set(cfg.get("review.ensemble.always_keep", []))

    def vote(self, voters: dict[str, list[Finding]]) -> list[Group]:
        groups: list[Group] = []
        for voter, findings in voters.items():
            for f in findings:
                group = next((g for g in groups if voter not in g.findings
                              and _same(next(iter(g.findings.values())), f)), None)
                if group is None:
                    group = Group()
                    groups.append(group)
                group.findings[voter] = f
        total = sum(self.weights.get(v, 1.0) for v in voters) or 1.0
        for g in groups:
            share = sum(self.weights.get(v, 1.0) for v in g.findings) / total
            g.kept = share >= self.threshold or g.representative.severity in self.always_keep
        return groups
