"""8. Quality scoring: quality score per requirement and accuracy / precision / recall / F1
of each reviewer measured against the ensemble consensus (or labelled ground truth)."""

from __future__ import annotations

import json
from pathlib import Path

from .state import Finding

SEVERITY_WEIGHTS = {"critical": 1.0, "high": 0.5, "medium": 0.2, "low": 0.05, "info": 0.0}


def quality_score(findings: list[Finding]) -> float:
    penalty = sum(SEVERITY_WEIGHTS.get(f.severity, 0.05) for f in findings)
    return round(max(0.0, 1.0 - penalty), 4)


def classification(tp: int, fp: int, fn: int, tn: int) -> dict[str, float]:
    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": round((tp + tn) / total, 4) if total else 1.0,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def confusion(groups: list[dict], voters: list[str]) -> dict[str, list[int]]:
    """groups: [{"voters": [...], "kept": bool}] -> voter -> [tp, fp, fn, tn]."""
    counts = {v: [0, 0, 0, 0] for v in voters}
    for g in groups:
        for v in voters:
            flagged, truth = v in g["voters"], g["kept"]
            idx = 0 if flagged and truth else 1 if flagged else 2 if truth else 3
            counts[v][idx] += 1
    return counts


def reviewer_metrics(groups: list[dict], voters: list[str]) -> dict[str, dict[str, float]]:
    return {v: classification(*c) for v, c in confusion(groups, voters).items()}


def benchmark(out_dir: Path | None = None, dataset: Path | None = None) -> dict:
    """Aggregate reviewer metrics from a labelled dataset or from `review-*.json` run outputs."""
    groups: list[dict] = []
    if dataset:
        data = json.loads(dataset.read_text(encoding="utf-8"))
        for item in data["items"]:
            groups.append({"voters": [v for v, p in item["predictions"].items() if p],
                           "kept": bool(item["truth"])})
        voters = sorted({v for item in data["items"] for v in item["predictions"]})
    else:
        files = sorted(Path(out_dir).rglob("review-*.json")) if out_dir else []
        voters_set: set[str] = set()
        for f in files:
            review = json.loads(f.read_text(encoding="utf-8"))
            groups.extend(review.get("groups", []))
            voters_set.update(review.get("voters", []))
        voters = sorted(voters_set)
    return {"items": len(groups), "reviewers": reviewer_metrics(groups, voters)}
