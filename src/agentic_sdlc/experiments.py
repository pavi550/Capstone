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


def summarize(directory, weights):
    manifest = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    trials = []
    for trial in manifest["trials"]:
        record_path = directory / f"{trial['id']}.json"
        measured = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else None
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
            comparisons.append({"trial": trial["id"], "comparison": result})
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
                run_id = store.create(draft)
                with closing(store._connect()) as connection, connection:
                    connection.execute("INSERT INTO experiment_runs VALUES (?, ?, ?)", (identity, trial["id"], run_id))
            trials.append({**trial, "run_id": run_id})
    return {"experiment_id": identity, "trials": trials}


def execute_trial(store, directory, trial_id, stage, *, config_path, output, acceptance=None, actor=None, allow_provider=False):
    """Use existing stage handlers and gates; no alternate experiment orchestrator."""
    import argparse

    from .cli import _dev_report, _dev_run, _dev_verification
    from .lifecycle import execute

    registered = schedule(store, directory)
    trial = next((row for row in registered["trials"] if row["id"] == trial_id), None)
    if trial is None:
        raise ValueError("Unknown scheduled trial")
    run_id = trial["run_id"]
    if stage == "cancel":
        return store.cancel(run_id)
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
    return execute(store, run_id, "trial-" + stage, lambda: handler(args))
