"""Evidence-based scoring and baseline comparisons for SDLC experiments."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from pathlib import Path

MODES = {"HUMAN_BASELINE", "AGENT_ASSISTED", "AGENT_DRIVEN"}
# These identify the task and independent conditions held constant in a comparison.
MATCH_FIELDS = ("task_id", "repository", "starting_commit", "spec_sha256", "acceptance_sha256")
MEASURES = ("delivery_seconds", "human_seconds", "total_cost")


def _number(value, label, *, maximum=math.inf):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= maximum or not math.isfinite(value)):
        raise ValueError(f"{label} must be a finite number between 0 and {maximum}")
    return value


def _rubric(config):
    if not isinstance(config, Mapping) or not isinstance(config.get("version"), str) or not config["version"]:
        raise ValueError("evaluation.rubric.version is required")
    criteria = config.get("criteria")
    if not isinstance(criteria, Mapping) or not criteria:
        raise ValueError("evaluation.rubric.criteria must be a nonempty mapping")
    weights = {}
    criteria_ids = set()
    for stage, group in criteria.items():
        if not isinstance(group, Mapping) or not group:
            raise ValueError("Each rubric stage must contain criteria")
        for criterion, weight in group.items():
            if criterion in criteria_ids:
                raise ValueError("Criterion IDs must be unique across stages")
            criteria_ids.add(criterion)
            points = _number(weight, "Criterion weight", maximum=100)
            if points == 0:
                raise ValueError("Criterion weight must be greater than zero")
            weights[stage, criterion] = points
    if not math.isclose(sum(weights.values()), 100, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Rubric weights must sum to 100")
    gates = config.get("mandatory_gates", [])
    if (not isinstance(gates, list) or not gates
            or not all(isinstance(gate, str) and gate for gate in gates)
            or len(set(gates)) != len(gates)):
        raise ValueError("Rubric must define unique mandatory gate IDs")
    return weights, gates


def _verify_refs(root: Path, refs):
    if not isinstance(refs, list) or not refs:
        raise ValueError("An evaluated criterion or gate requires at least one evidence reference")
    resolved = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise TypeError("Evidence references must include a path and SHA-256 digest")
        path, expected = ref.get("path"), ref.get("sha256")
        if (not isinstance(path, str) or not isinstance(expected, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise ValueError("Evidence references must include a path and SHA-256 digest")
        relative = Path(path)
        if (relative.is_absolute() or relative.drive
                or any(part.lower() == ".git" or part.lower().startswith(".env")
                       for part in relative.parts)):
            raise ValueError("Evidence must use an allowed relative path")
        file = (root / relative).resolve()
        if (not file.is_relative_to(root) or not file.is_file()
                or any(part.lower() == ".git" or part.lower().startswith(".env")
                       for part in file.relative_to(root).parts)):
            raise ValueError("Evidence file is missing or outside allowed evidence paths")
        with file.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("Evidence digest mismatch")
        resolved.append({"path": relative.as_posix(), "sha256": actual})
    return resolved


def score(config, assessment, root: Path):
    """Score externally supplied assessments and verify referenced evidence files.

    The caller selects the independent evaluator's directory. Digests bind files to
    the assessment and do not authenticate an evaluator or its judgements.
    """
    root = root.resolve()
    weights, required_gates = _rubric(config)
    inputs = assessment.get("criteria", {})
    gates = assessment.get("gates", {})
    evaluator = assessment.get("evaluator_version")
    if not isinstance(evaluator, str) or not evaluator.strip():
        raise ValueError("An evaluator_version is required")
    if not isinstance(inputs, Mapping) or set(inputs) - {criterion for _, criterion in weights}:
        raise ValueError("Assessment contains invalid or unknown criterion IDs")
    if not isinstance(gates, Mapping) or set(gates) - set(required_gates):
        raise ValueError("Assessment contains invalid or unknown gate IDs")
    rows = []
    for (stage, criterion), maximum in weights.items():
        entry = inputs.get(criterion)
        row = {"stage": stage, "id": criterion, "maximum": maximum, "earned": None,
               "status": "NOT_EVALUATED", "evidence": [], "evaluated": False,
               "reason": "No verified measurement", "method": None, "evaluator_version": evaluator}
        if entry is not None:
            if not isinstance(entry, Mapping):
                raise ValueError("Criterion assessments must be objects")
            row["evidence"] = _verify_refs(root, entry.get("evidence"))
            reason, method = entry.get("reason"), entry.get("method")
            if not all(isinstance(value, str) and value.strip() for value in (reason, method)):
                raise ValueError("Every assessed criterion needs a reason and method")
            row.update(reason=reason, method=method)
            if entry.get("status") == "NOT_APPLICABLE":
                if "ratio" in entry:
                    raise ValueError("An inapplicable criterion cannot have a ratio")
                row["status"] = "NOT_APPLICABLE"
            else:
                ratio = _number(entry.get("ratio"), "Criterion ratio", maximum=1)
                status = "PASSED" if ratio == 1 else "FAILED" if ratio == 0 else "PARTIALLY_PASSED"
                row.update(earned=round(ratio * maximum, 6), status=status, evaluated=True)
        rows.append(row)

    gate_results = {}
    for name in required_gates:
        entry = gates.get(name)
        if entry is None:
            gate_results[name] = {"status": "NOT_EVALUATED", "evidence": []}
        elif isinstance(entry, Mapping) and type(entry.get("passed")) is bool:
            gate_results[name] = {
                "status": "PASSED" if entry["passed"] else "FAILED",
                "evidence": _verify_refs(root, entry.get("evidence")),
            }
        else:
            raise ValueError("A gate assessment requires a boolean passed value")
    unassessed = any(row["status"] == "NOT_EVALUATED" for row in rows)
    gate_statuses = {gate["status"] for gate in gate_results.values()}
    evaluated_maximum = sum(row["maximum"] for row in rows if row["evaluated"])
    applicable_maximum = sum(row["maximum"] for row in rows if row["status"] != "NOT_APPLICABLE")
    delivery = (
        "BLOCKED" if "FAILED" in gate_statuses else
        "PENDING" if unassessed or "NOT_EVALUATED" in gate_statuses else "ELIGIBLE"
    )
    return {
        "rubric_version": config["version"], "evaluator_version": evaluator,
        "earned_points": round(sum(row["earned"] or 0 for row in rows), 6),
        "configured_maximum": 100, "evaluated_maximum": evaluated_maximum,
        "applicable_maximum": applicable_maximum,
        "evidence_completeness": evaluated_maximum / applicable_maximum if applicable_maximum else None,
        "status": "PROVISIONAL" if unassessed or "NOT_EVALUATED" in gate_statuses else "FINAL",
        "delivery_eligibility": delivery, "criteria": rows, "gates": gate_results,
    }


def compare(baseline, actual, index_weights=None):
    """Compare measured records from equivalent runs without filling missing inputs."""
    if baseline.get("mode") != "HUMAN_BASELINE" or actual.get("mode") not in MODES - {"HUMAN_BASELINE"}:
        raise ValueError("Comparison requires a HUMAN_BASELINE and an assisted or driven run")
    for field in MATCH_FIELDS:
        value = baseline.get(field)
        if not isinstance(value, str) or not value.strip() or value != actual.get(field):
            raise ValueError(f"Comparable records must have identical {field}")
        if field.endswith("_sha256") and not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError(f"{field} must be a SHA-256 digest")
    values = {}
    for name in MEASURES:
        pair = [record.get(name) for record in (baseline, actual)]
        values[name] = [None if value is None else _number(value, name) for value in pair]
    if all(value is not None for value in values["total_cost"]):
        currency = baseline.get("currency")
        if not isinstance(currency, str) or not currency or currency != actual.get("currency"):
            raise ValueError("Cost comparisons require matching currencies")

    def reduction(pair):
        before, after = pair
        return (before - after) / before * 100 if before and after is not None else None

    before, after = values["delivery_seconds"]
    return {
        "task_id": baseline["task_id"], "baseline_mode": baseline["mode"], "actual_mode": actual["mode"],
        "identity": {field: baseline[field] for field in MATCH_FIELDS},
        "measurements": values, "currency": baseline.get("currency"),
        "delivery_time_reduction_pct": reduction(values["delivery_seconds"]),
        "human_effort_saved_pct": reduction(values["human_seconds"]),
        "cost_reduction_pct": reduction(values["total_cost"]),
        "delivery_speedup": before / after if before is not None and after else None,
        "overall_productivity_index": _productivity_index(baseline, actual, values, index_weights),
    }


def _productivity_index(baseline, actual, measures, weights):
    if weights is None:
        return None
    components = {"quality", "delivery_time", "human_effort", "cost"}
    if not isinstance(weights, Mapping) or set(weights) != components:
        raise ValueError("Productivity index weights must cover quality, delivery_time, human_effort and cost")
    weights = {name: _number(value, "Productivity weight", maximum=1) for name, value in weights.items()}
    if not math.isclose(sum(weights.values()), 1, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Productivity index weights must sum to 1")
    baseline_quality = baseline.get("deliverable_quality")
    actual_quality = actual.get("deliverable_quality")
    if baseline_quality is None or actual_quality is None:
        return None
    if not isinstance(baseline_quality, Mapping) or not isinstance(actual_quality, Mapping):
        raise TypeError("Deliverable quality must include score and rubric_version")
    revision = baseline_quality.get("rubric_version")
    if not isinstance(revision, str) or not revision or revision != actual_quality.get("rubric_version"):
        raise ValueError("Productivity index requires matching deliverable-only rubric versions")
    _number(baseline_quality.get("score"), "Baseline deliverable score", maximum=100)
    scores = {"quality": _number(actual_quality.get("score"), "Deliverable score", maximum=100)}
    for component, measure in zip(("delivery_time", "human_effort", "cost"), MEASURES, strict=True):
        before, after = measures[measure]
        if before is None or after is None or max(before, after) == 0:
            return None
        # Scaling keeps the denominator finite for large measured values.
        scale = max(before, after)
        scores[component] = 100 * (before / scale) / (before / scale + after / scale)
    return {
        "score": round(sum(weights[key] * scores[key] for key in scores), 6),
        "components": scores, "weights": weights, "quality_rubric_version": revision,
    }
