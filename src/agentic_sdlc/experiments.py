"""Local reproducibility manifests and independently measured trial records."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from .development import _manifest, _snapshot
from .evaluation import compare

MODES = ("HUMAN_BASELINE", "AGENT_ASSISTED", "AGENT_DRIVEN")


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)


def prepare(plan, source: Path, output: Path):
    task = plan.get("task_id")
    repeat = plan.get("repetitions")
    if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task):
        raise ValueError("An experiment needs a portable task ID")
    if type(repeat) is not int or not 1 <= repeat <= 10:
        raise ValueError("An experiment needs between 1 and 10 repetitions")
    for field, pattern in (("starting_commit", r"[a-f0-9]{40}"), ("spec_sha256", r"[a-f0-9]{64}"),
                           ("acceptance_sha256", r"[a-f0-9]{64}")):
        if not re.fullmatch(pattern, str(plan.get(field, ""))):
            raise ValueError(f"Invalid experiment identity field: {field}")
    if any(not isinstance(plan.get(field), str) or not plan[field].strip()
           for field in ("repository", "complexity", "rubric_version")):
        raise ValueError("Record the repository, task complexity and rubric version")
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Keep experiment output separate from the source workspace")
    output.mkdir()
    baseline = output / "baseline"
    _snapshot(source, baseline)
    starting_sha256 = _digest(_manifest(baseline))
    runs = []
    for repetition in range(1, repeat + 1):
        for mode in MODES:
            run_id = f"{repetition:02d}-{mode.lower()}"
            _snapshot(baseline, output / run_id)
            if _digest(_manifest(output / run_id)) != starting_sha256:
                raise ValueError("Experiment replica differs from its baseline")
            runs.append({"id": run_id, "mode": mode, "repetition": repetition})
    result = {"plan": plan, "source_manifest_sha256": starting_sha256, "trials": runs,
              "created_at": datetime.now(UTC).isoformat()}
    _write(output / "experiment.json", result)
    return result


def record(directory, trial_id, measured):
    manifest = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    trial = next((trial for trial in manifest["trials"] if trial["id"] == trial_id), None)
    if trial is None:
        raise ValueError("Unknown experiment trial")
    if measured.get("mode") != trial["mode"] or measured.get("status") not in {
        "VALIDATED", "FAILED", "BLOCKED", "CANCELLED",
    }:
        raise ValueError("Record the correct execution mode and a measured terminal outcome")
    for field in ("task_id", "repository", "starting_commit", "spec_sha256", "acceptance_sha256"):
        if measured.get(field) != manifest["plan"][field]:
            raise ValueError(f"Measurement does not match the experiment: {field}")
    _write(directory / f"{trial_id}.json", measured)
    return measured


def submit_baseline(directory, trial_id, submission, deliverable, authority):
    actor, measured = submission["actor"], submission["payload"]
    manifest = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    if measured.get("mode") != "HUMAN_BASELINE" or measured.get("source") != "ACTUAL_HUMAN":
        raise ValueError("Baseline submission requires measured human work")
    if (measured.get("experiment_id") != _digest(manifest) or measured.get("trial_id") != trial_id
            or measured.get("human_actor") == actor or not measured.get("human_actor")):
        raise ValueError("Baseline must bind the experiment, trial and independent verifier")
    if not authority.validate_seal("verifiers", "human-baseline", actor, measured, submission["mac"]):
        raise ValueError("Invalid independent baseline attestation")
    if measured.get("artifact_sha256") != _digest(_manifest(deliverable.resolve())):
        raise ValueError("Baseline deliverable does not match the verifier attestation")
    if (not measured.get("verification_evidence") or any(type(measured.get(k)) not in {int, float}
            or not math.isfinite(measured[k]) or measured[k] < 0 for k in ("human_seconds", "delivery_seconds"))):
        raise ValueError("Baseline requires independent evidence and measured durations")
    return record(directory, trial_id, {**measured, "verifier": actor, "verifier_mac": submission["mac"]})


def _managed_measurement(directory, manifest, trial):
    folder = directory / "execution-evidence" / trial["id"]
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")]
    if not snapshots:
        return None
    snapshot = max(snapshots, key=lambda row: row["observed_at"])
    receipts, stages = snapshot["receipts"], snapshot["stages"]
    validation = receipts.get("validation_v2") or receipts.get("validation") or {}
    outcome = validation.get("status") or snapshot["outcome"]
    status = {"LOCAL_DEV_VALIDATED": "VALIDATED", "REMOTE_DEV_VALIDATED": "VALIDATED",
              "KeyboardInterrupt": "INTERRUPTED", "StageCancelled": "CANCELLED"}.get(outcome, outcome)
    final = receipts.get("final_score") or {}
    if status == "VALIDATED" and final.get("delivery_eligibility") != "ELIGIBLE":
        status = "BLOCKED"
    ended = [row["finished_at"] for row in stages if row.get("finished_at")]
    started = [row["started_at"] for row in stages]
    duration = ((datetime.fromisoformat(max(ended)) - datetime.fromisoformat(min(started))).total_seconds()
                if started and ended else None)
    usage = [row.get("normalized_usage") or {} for row in snapshot.get("provider_requests", [])]
    provider_tokens = [_measured_total(usage, field) for field in ("input_tokens", "output_tokens")]
    provider_cost = _measured_total(usage, "estimated_cost")
    currencies = {row.get("currency") for row in usage}
    if len(currencies) != 1 or None in currencies:
        provider_cost = None
    human_kinds = snapshot["human_effort"].get("measured_seconds_by_kind", {})
    review_receipt = receipts.get("ci_review_evidence") or receipts.get("ci_evidence") or {}
    review_metrics = ((review_receipt.get("payload") or {}).get("evaluator", {}).get("review_evaluation") or {}).get("metrics")
    if review_metrics is None:
        review_metrics = ((receipts.get("local_review_evidence") or {}).get("payload") or {}).get("metrics")
    return {**{key: manifest["plan"][key] for key in
               ("task_id", "repository", "starting_commit", "spec_sha256", "acceptance_sha256")},
            "mode": trial["mode"], "status": status, "delivery_seconds": duration,
            "human_seconds": snapshot["human_effort"]["measured_human_seconds"], "total_cost": None,
            "aggregate_agent_seconds": snapshot.get("timing", {}).get("aggregate_agent_seconds"),
            "approval_wait_seconds": snapshot.get("approval_wait_seconds"),
            "human_active_seconds_by_kind": human_kinds,
            "first_pass_quality": (receipts.get("first_pass_score") or {}).get("earned_points"),
            "final_quality": final.get("earned_points"), "review_metrics": review_metrics,
            "correction_attempts": snapshot.get("correction_attempts"),
            "guardrail_events": snapshot.get("guardrail_events"),
            "test_outcomes": (receipts.get("actual_verification") or {}).get("payload", {}).get("checks"),
            "dev_validation_status": validation.get("status"),
            "provider_tokens": sum(provider_tokens) if None not in provider_tokens else None,
            "estimated_provider_cost": provider_cost,
            "provider_cost_currency": next(iter(currencies)) if provider_cost is not None else None,
            "evidence": snapshot}


def summarize(directory, weights):
    manifest = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    trials = []
    for trial in manifest["trials"]:
        record_path = directory / f"{trial['id']}.json"
        measured = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else _managed_measurement(directory, manifest, trial)
        trials.append({**trial, "measurement": measured})
    by_repetition = {trial["repetition"]: trial["measurement"] for trial in trials if trial["mode"] == MODES[0]}
    comparisons = []
    for trial in trials:
        actual = trial["measurement"]
        baseline = by_repetition[trial["repetition"]]
        if trial["mode"] != MODES[0]:
            result = None
            if actual and baseline and actual["status"] == baseline["status"] == "VALIDATED":
                result = compare(baseline, actual, weights)
            missing = []
            if not baseline or baseline["status"] != "VALIDATED":
                missing.append("validated_baseline")
            if not actual or actual["status"] != "VALIDATED":
                missing.append("validated_trial")
            if result:
                missing.extend(name for name, pair in result["measurements"].items() if None in pair)
                if result["overall_productivity_index"] is None:
                    missing.append("comparable_productivity_index_inputs")
            comparisons.append({"trial": trial["id"], "comparison": result, "missing_evidence": missing})
    summaries = {}
    for mode in MODES:
        measured = [trial["measurement"] for trial in trials if trial["mode"] == mode and trial["measurement"]]
        validated = sum(record["status"] == "VALIDATED" for record in measured)
        first_pass = [record["first_pass_success"] for record in measured if type(record.get("first_pass_success")) is bool]
        human_seconds = _measured_total(measured, "human_seconds")
        costs = _measured_total(measured, "total_cost")
        currencies = {record.get("currency") for record in measured}
        summaries[mode] = {"recorded": len(measured), "validated": validated,
                           "final_success_rate": validated / len(measured) if measured else None,
                           "first_pass_success_rate": sum(first_pass) / len(first_pass) if first_pass else None,
                           "validated_tasks_per_human_hour": validated * 3600 / human_seconds if human_seconds else None,
                           "cost_per_validated_task": costs / validated if validated and costs is not None
                           and len(currencies) == 1 and None not in currencies else None}
    return {"experiment": manifest, "trials": trials, "comparisons": comparisons, "mode_summaries": summaries}


def export_report(directory, report):
    import csv

    directory.mkdir()
    _write(directory / "experiment.json", report)
    comparisons = {row["trial"]: row for row in report["comparisons"]}
    fields = ("trial", "mode", "status", "delivery_seconds", "human_seconds", "provider_tokens",
              "estimated_provider_cost", "aggregate_agent_seconds", "approval_wait_seconds",
              "human_active_seconds_by_kind", "first_pass_quality", "final_quality", "review_metrics",
              "correction_attempts", "guardrail_events", "test_outcomes", "dev_validation_status",
              "missing_evidence", "comparison")
    rows = []
    for trial in report["trials"]:
        measurement = trial["measurement"] or {}
        rows.append({**{field: measurement.get(field) for field in fields[2:-2]},
                     "trial": trial["id"], "mode": trial["mode"],
                     "missing_evidence": comparisons.get(trial["id"], {}).get("missing_evidence"),
                     "comparison": comparisons.get(trial["id"], {}).get("comparison")})
    with (directory / "experiment.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    with (directory / "experiment.md").open("x", encoding="utf-8") as stream:
        stream.write("# Experiment report\n\n| " + " | ".join(fields) + " |\n|"
                     + " --- |" * len(fields) + "\n")
        for row in rows:
            values = [json.dumps(row[field], ensure_ascii=False) if row[field] is not None else "Unavailable"
                      for field in fields]
            stream.write("| " + " | ".join(value.replace("|", "&#124;") for value in values) + " |\n")


def _measured_total(records, key):
    values = [record.get(key) for record in records]
    if not values or any(type(value) not in {int, float} or not math.isfinite(value) or value < 0 for value in values):
        return None
    return sum(values)


def schedule(store, directory):
    """Idempotently register prepared trials in the existing run and budget store."""
    import sqlite3
    from contextlib import closing

    from .specifications import specification_digest

    directory = directory.resolve()
    manifest = json.loads((directory / "experiment.json").read_text())
    identity = _digest(manifest)
    specification = json.loads((directory / "requirement.json").read_text())
    if hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest() != manifest["plan"]["spec_sha256"]:
        raise ValueError("Experiment specification changed")
    lock_path = store.path.with_name(f".{store.path.name}.experiment-{identity}.lock")
    with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        trials = []
        for trial in manifest["trials"]:
            with closing(store._connect()) as connection:
                old = connection.execute("SELECT run_id FROM experiment_runs WHERE experiment_id=? AND trial_id=?",
                                         (identity, trial["id"])).fetchone()
            if old:
                run_id = old[0]
            else:
                workspace = directory / trial["id"]
                if _digest(_manifest(workspace)) != manifest["source_manifest_sha256"]:
                    raise ValueError("Trial changed before registration")
                draft = {"issue": manifest["plan"]["task_id"], "title": specification["requirement"],
                         "description": specification["requirement"], "source": "controlled_experiment",
                         "acceptance_criteria": [{"id": f"AC-{index}", "text": text} for index, text in
                                                 enumerate(specification["acceptance_criteria"], 1)],
                         "status": "AWAITING_APPROVAL"}
                draft["spec_sha256"] = specification_digest(draft)
                run_id = store.create(draft, trial=(identity, trial["id"]))
            trials.append({**trial, "run_id": run_id})
    return {"experiment_id": identity, "trials": trials}


def cancel_experiment(store, experiment_id):
    """Cancel registered runs using the same durable cancellation as individual trials."""
    from contextlib import closing

    with closing(store._connect()) as connection:
        trials = connection.execute(
            "SELECT trial_id, run_id FROM experiment_runs WHERE experiment_id=? ORDER BY trial_id",
            (experiment_id,),
        ).fetchall()
    if not trials:
        raise ValueError("Unknown experiment")
    outcomes = []
    for trial_id, run_id in trials:
        try:
            result = store.cancel(run_id)
        except ValueError as exc:
            outcomes.append({"trial_id": trial_id, "run_id": run_id, "status": "UNCHANGED", "reason": str(exc)})
        else:
            outcomes.append({"trial_id": trial_id, **result})
    return {"experiment_id": experiment_id, "trials": outcomes}


def enforce_policy(store, run_id, config, *, expected=None):
    from contextlib import closing

    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        trial = connection.execute("SELECT experiment_id FROM experiment_runs WHERE run_id=?", (run_id,)).fetchone()
        if not trial:
            return
        # Store only a digest. Effective config can contain deployment and provider details.
        effective = _digest(config.data)
        if expected is not None and effective != expected:
            raise ValueError("Trial configuration differs from the experiment plan")
        policy = connection.execute("SELECT digest FROM experiment_execution_policy WHERE experiment_id=?",
                                    (trial[0],)).fetchone()
        if policy is None:
            if expected is None:
                raise ValueError("Start the managed trial with its frozen experiment policy")
            connection.execute("INSERT INTO experiment_execution_policy VALUES (?, ?, ?)",
                               (trial[0], effective, json.dumps({"effective_configuration_sha256": effective})))
        elif policy[0] != effective:
            raise ValueError("Trial configuration differs from the frozen experiment policy")


def _trial_snapshot(directory, trial, store, outcome):
    from uuid import uuid4

    from .lifecycle import timing_summary

    stages = store.lifecycle_stages(trial["run_id"])
    record = store.get(trial["run_id"])
    reference = {key: store.receipt(trial["run_id"], key, optional=True)
                 for key in ("first_pass_score", "final_score", "actual_verification", "ci_evidence", "ci_review_evidence",
                             "validation", "validation_v2", "local_review_evidence")}
    folder = directory / "execution-evidence" / trial["id"]
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / (uuid4().hex + ".json"), {
        "trial": trial, "outcome": outcome, "observed_at": datetime.now(UTC).isoformat(),
        "specification_sha256": record["digest"], "approval_wait_seconds": record["approval_wait_seconds"],
        "human_effort": store.human_effort(trial["run_id"]), "stages": stages, "timing": timing_summary(stages),
        "provider_requests": store.provider_requests(trial["run_id"]), "receipts": reference,
        "correction_attempts": sum(bool((row["evidence"] or {}).get("remediation")) for row in
                                   record["development_attempts"]) if record["development_attempts"] else None,
        "guardrail_events": [event for row in record["development_attempts"]
                             for event in (row["evidence"] or {}).get("guardrail_events", [])] or None,
    })


def execute_trial(store, directory, trial_id, stage, *, config_path, output, acceptance=None, actor=None, allow_provider=False):
    """Use existing stage handlers and gates; no alternate experiment orchestrator."""
    import argparse

    from .cli import _dev_report, _dev_run, _dev_verification
    from .config import Config
    from .lifecycle import execute

    registered = schedule(store, directory)
    trial = next((row for row in registered["trials"] if row["id"] == trial_id), None)
    if trial is None:
        raise ValueError("Unknown scheduled trial")
    run_id = trial["run_id"]
    if stage == "cancel":
        result = store.cancel(run_id)
        _trial_snapshot(directory, trial, store, result["status"])
        return result
    if stage == "status":
        return {"trial": trial, "state": store.get(run_id), "stages": store.lifecycle_stages(run_id)}
    if trial["mode"] == "HUMAN_BASELINE" and stage in {"run", "resume", "correct", "review"}:
        raise ValueError("Human baseline work must be performed and submitted by a human")
    handler = {"run": _dev_run, "resume": _dev_run, "correct": _dev_run,
               "testing": _dev_verification, "review": _dev_verification,
               "validate": _dev_verification, "report": _dev_report}.get(stage)
    if handler is None:
        raise ValueError("Unsupported managed trial stage")
    args = argparse.Namespace(dev_command=stage, run_id=run_id, store=store.path, config=config_path,
                              repo=directory / trial_id, out=output / trial_id, allow_provider=allow_provider,
                              actor=actor, acceptance_dir=acceptance, authorize_remote_dev=False, report_dir=None, output=None)
    if stage != "report":
        plan = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))["plan"]
        expected = plan.get("effective_configuration_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Bind the experiment plan to an effective configuration digest before execution")
        enforce_policy(store, run_id, Config.load(config_path, local_model=allow_provider), expected=expected)
    try:
        result = execute(store, run_id, "trial-" + stage, lambda: handler(args))
    except BaseException as exc:
        _trial_snapshot(directory, trial, store, type(exc).__name__)
        raise
    _trial_snapshot(directory, trial, store, result.get("status", "COMPLETED"))
    return result
