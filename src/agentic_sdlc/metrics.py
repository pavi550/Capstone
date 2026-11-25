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
def guardrail_effectiveness(observations: list[dict]) -> dict:
    """Separate preventive runtime outcomes from post-execution detection."""
    phases = {
        "runtime": ("BLOCKED", "ALLOWED"),
        "post_execution": ("DETECTED", "MISSED"),
    }
    if not isinstance(observations, list):
        raise TypeError("Guardrail observations must be a list")
    outcomes = {phase: [] for phase in phases}
    seen = set()
    for row in observations:
        if not isinstance(row, dict):
            raise TypeError("Guardrail observations must be objects")
        phase, identifier = row.get("phase"), row.get("id")
        truth, decision = row.get("violation"), row.get("decision")
        if (not isinstance(phase, str) or phase not in phases
                or not isinstance(identifier, str) or not identifier
                or decision not in phases[phase] or (truth is not None and type(truth) is not bool)):
            raise ValueError("Guardrail observation needs a valid phase, ID, decision and independent label")
        if (phase, identifier) in seen:
            raise ValueError("Guardrail observation IDs must be unique within each phase")
        seen.add((phase, identifier))
        outcomes[phase].append((truth, decision == phases[phase][0]))

    result = {}
    for phase, rows in outcomes.items():
        violations = sum(truth is True for truth, _ in rows)
        legitimate = sum(truth is False for truth, _ in rows)
        caught = sum(truth is True and decision for truth, decision in rows)
        false_actions = sum(truth is False and decision for truth, decision in rows)
        counts = {
            "observations": len(rows), "labelled_violations": violations,
            "labelled_legitimate": legitimate, "unlabelled": len(rows) - violations - legitimate,
        }
        if phase == "runtime":
            result[phase] = {
                **counts, "blocked": caught, "escaped": violations - caught,
                "false_blocks": false_actions,
                "agent_compliance_pct": _percent(legitimate, violations + legitimate),
                "block_rate_pct": _percent(caught, violations),
                "escape_rate_pct": _percent(violations - caught, violations),
                "false_block_rate_pct": _percent(false_actions, legitimate),
            }
        else:
            result[phase] = {
                **counts, "detected": caught, "missed": violations - caught,
                "false_alerts": false_actions, "detection_rate_pct": _percent(caught, violations),
                "false_alert_rate_pct": _percent(false_actions, legitimate),
            }
    return result


def _percent(numerator, denominator):
    return round(100 * numerator / denominator, 6) if denominator else None


def independently_reviewed(defects: list[str], findings: list[dict]) -> dict:
    """Measure origin-specific recall against labelled defects and adjudicated findings."""
    if not isinstance(defects, list) or not isinstance(findings, list):
        raise TypeError("Defects and findings must be lists")
    if (not all(isinstance(defect, str) and defect for defect in defects)
            or len(set(defects)) != len(defects)):
        raise ValueError("Ground-truth defects require unique IDs")
    truth = set(defects)
    reviewers: dict[str, dict[str, set[str]]] = {}
    seen = set()
    for row in findings:
        if not isinstance(row, dict):
            raise TypeError("Review findings must be objects")
        origin, identifier = row.get("origin"), row.get("id")
        adjudication, defect = row.get("adjudication"), row.get("defect_id")
        if (origin not in ("ai", "human", "tool") or not isinstance(identifier, str) or not identifier
                or adjudication not in ("confirmed", "incorrect", "unverified")):
            raise ValueError("Review finding needs an ID, origin and independent adjudication")
        if (origin, identifier) in seen:
            raise ValueError("Review finding IDs must be unique within each origin")
        seen.add((origin, identifier))
        bucket = reviewers.setdefault(origin, {name: set() for name in (
            "confirmed", "incorrect", "unverified", "derived",
        )})
        if adjudication == "confirmed" and (not isinstance(defect, str) or defect not in truth):
            raise ValueError("A confirmed finding must identify a ground-truth defect")
        if row.get("derived_from"):
            bucket["derived"].add(identifier)
        else:
            bucket[adjudication].add(defect if adjudication == "confirmed" else identifier)

    result = {}
    for origin, bucket in reviewers.items():
        tp, fp = len(bucket["confirmed"]), len(bucket["incorrect"])
        fn = len(truth) - tp
        result[origin] = {
            "tp": tp, "fp": fp, "fn": fn, "unverified": len(bucket["unverified"]),
            "derived": len(bucket["derived"]),
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        }
    return result
