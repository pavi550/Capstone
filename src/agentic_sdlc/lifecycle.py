"""Persistent stage journal and cooperative cancellation for stored enterprise runs."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

current_stage = ContextVar("enterprise_stage", default=None)
_interval = ContextVar("enterprise_interval", default=None)


class StageCancelled(RuntimeError):
    pass


class CleanupUnresolved(RuntimeError):
    """The owned resource may still be active."""


def _stage_cancelled(connection, run_id, stage):
    return connection.execute(
        "SELECT 1 FROM lifecycle_stage_cancellations WHERE run_id=? "
        "AND (stage=? OR substr(?,1,length(stage)+1)=stage || '/')",
        (run_id, stage, stage),
    ).fetchone() is not None


def cancel_stage(store, run_id, stage, *, requester=None, reason=None):
    if not re.fullmatch(r"[A-Za-z0-9_/-]{1,200}", stage):
        raise ValueError("Use the exact persisted stage name")
    stamp = datetime.now(UTC).isoformat()
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        store._get(connection, run_id)
        active = connection.execute(
            "SELECT 1 FROM lifecycle_stages WHERE run_id=? AND state='RUNNING' "
            "AND (stage=? OR substr(stage,1,length(?)+1)=? || '/')",
            (run_id, stage, stage, stage),
        ).fetchone()
        connection.execute("INSERT OR IGNORE INTO lifecycle_stage_cancellations VALUES (?, ?, ?, ?, ?, ?)",
                           (run_id, stage, stamp, requester, reason, None if active else stamp))
    return {"run_id": run_id, "stage": stage, "status": "CANCELLING" if active else "CANCELLED"}


def checkpoint():
    context = current_stage.get()
    if context:
        store, run_id, stage, attempt = context
        with closing(store._connect()) as connection:
            if store._cancelled(connection, run_id) or _stage_cancelled(connection, run_id, stage):
                raise StageCancelled("Enterprise run cancelled")
            interval = _interval.get()
            now = monotonic()
            if interval and now - interval[1] >= 1:
                with connection:
                    connection.execute("UPDATE lifecycle_intervals SET heartbeat_at=?, duration_seconds=? "
                                       "WHERE run_id=? AND stage=? AND attempt=?",
                                       (datetime.now(UTC).isoformat(), now - interval[0], run_id, stage, attempt))
                _interval.set((interval[0], now))


def _lock_path(store, run_id):
    return store.path.with_name(f".{store.path.name}.{run_id}.lifecycle.lock")


def _recover_owned(connection, run_id):
    """Call only while holding the exclusive run lock."""
    stamp = datetime.now(UTC).isoformat()
    active = connection.execute(
        "SELECT stage, attempt FROM lifecycle_stages WHERE run_id=? AND state='RUNNING'", (run_id,),
    ).fetchall()
    decisions = []
    for stage, attempt in active:
        # A vanished local owner proves interruption, not remote operation outcome.
        external = stage.split("/")[0] not in {"requirements", "report", "score"}
        action = "REQUIRE_OPERATOR_ACTION" if external else "RESTART_STAGE"
        row = (uuid4().hex, run_id, stage, attempt, stamp, "RUNNING", "EXCLUSIVE_LOCK_REACQUIRED",
               "NOT_ASSESSED", "UNCERTAIN" if external else "NOT_APPLICABLE", action, stamp)
        connection.execute("INSERT INTO lifecycle_recovery VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        connection.execute("UPDATE lifecycle_stages SET state='INTERRUPTED', failure='PROCESS_INTERRUPTED' "
                           "WHERE run_id=? AND stage=? AND attempt=?", (run_id, stage, attempt))
        decisions.append({"stage": stage, "attempt": attempt, "action": action})
    return decisions


def recover(store):
    """Assess abandoned attempts without repeating external side effects."""
    with closing(store._connect()) as connection:
        runs = connection.execute("SELECT DISTINCT run_id FROM lifecycle_stages WHERE state='RUNNING'").fetchall()
    results = []
    for (run_id,) in runs:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            continue
        with closing(sqlite3.connect(_lock_path(store, run_id), timeout=0)) as lock:
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                    raise
                results.append({"run_id": run_id, "action": "WAIT_FOR_ACTIVE_WORKER"})
                continue
            with closing(store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                results.extend({"run_id": run_id, **row} for row in _recover_owned(connection, run_id))
    return results


def recovery_history(store, run_id):
    with closing(store._connect()) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM lifecycle_recovery WHERE run_id=? ORDER BY rowid", (run_id,),
        )]


def execute(store, run_id, stage, operation):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid lifecycle run ID")
    parent = current_stage.get()
    nested = parent is not None and parent[0].path == store.path and parent[1] == run_id
    lock_path = _lock_path(store, run_id)
    with (nullcontext(None) if nested else closing(sqlite3.connect(lock_path, timeout=0))) as lock:
        try:
            if lock is not None:
                lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            raise ValueError("Another lifecycle stage is active for this run") from None
        with closing(store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            store._get(connection, run_id)
            if store._cancelled(connection, run_id) or _stage_cancelled(connection, run_id, stage):
                raise StageCancelled("Enterprise run cancelled")
            stamp = datetime.now(UTC).isoformat()
            if not nested:
                _recover_owned(connection, run_id)
            attempt = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0)+1 FROM lifecycle_stages WHERE run_id=? AND stage=?",
                (run_id, stage),
            ).fetchone()[0]
            connection.execute("INSERT INTO lifecycle_stages VALUES (?, ?, ?, ?, NULL, 'RUNNING', NULL, NULL)",
                               (run_id, stage, attempt, stamp))
            connection.execute("INSERT INTO lifecycle_intervals VALUES (?, ?, ?, ?, ?, NULL)",
                               (run_id, stage, attempt, str(os.getpid()), stamp))
        started = monotonic()
        token = current_stage.set((store, run_id, stage, attempt))
        interval_token = _interval.set((started, started))
        status, failure, references = "FAILED", None, None
        try:
            result = operation()
            data = result if isinstance(result, dict) else {}
            references = {key: data[key] for key in ("manifest_sha256", "artifact_dir", "review_sha256",
                                                       "spec_sha256", "status") if key in data}
            checkpoint()
            if nested:
                references["parent_stage"], references["parent_attempt"] = parent[2:]
            outcome = data.get("status")
            if outcome in {"BLOCKED", "FAILED", "CANCELLED"}:
                status = outcome
            elif outcome in {"AWAITING_APPROVAL", "WAITING_REVIEW_APPROVAL"}:
                status = "WAITING_FOR_APPROVAL"
            else:
                status = "COMPLETED"
            return result
        except BaseException as exc:
            failure = type(exc).__name__
            if isinstance(exc, CleanupUnresolved):
                status = "BLOCKED"
            elif isinstance(exc, StageCancelled):
                status = "CANCELLED"
            elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
                status = "INTERRUPTED"
            raise
        finally:
            _interval.reset(interval_token)
            current_stage.reset(token)
            with closing(store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE lifecycle_intervals SET duration_seconds=?, heartbeat_at=? "
                    "WHERE run_id=? AND stage=? AND attempt=?",
                    (monotonic() - started, datetime.now(UTC).isoformat(), run_id, stage, attempt),
                )
                if store._cancelled(connection, run_id) or _stage_cancelled(connection, run_id, stage):
                    pending = connection.execute(
                        "SELECT 1 FROM lifecycle_containers WHERE run_id=? AND cleanup_state!='VERIFIED_ABSENT'",
                        (run_id,),
                    ).fetchone()
                    if pending or failure == "CleanupUnresolved":
                        status, failure = "BLOCKED", "CleanupUnresolved"
                    else:
                        status = "CANCELLED"
                connection.execute(
                    "UPDATE lifecycle_stages SET finished_at=?, state=?, failure=?, refs=? "
                    "WHERE run_id=? AND stage=? AND attempt=?",
                    (datetime.now(UTC).isoformat(), status, failure, json.dumps(references), run_id, stage, attempt),
                )
                connection.execute(
                    "UPDATE lifecycle_stage_cancellations SET completed_at=? WHERE run_id=? AND completed_at IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM lifecycle_containers c WHERE c.run_id=lifecycle_stage_cancellations.run_id "
                    "AND c.cleanup_state!='VERIFIED_ABSENT') "
                    "AND NOT EXISTS (SELECT 1 FROM lifecycle_stages s WHERE s.run_id=? AND s.state='RUNNING' "
                    "AND (s.stage=lifecycle_stage_cancellations.stage OR substr(s.stage,1,"
                    "length(lifecycle_stage_cancellations.stage)+1)=lifecycle_stage_cancellations.stage || '/'))",
                    (datetime.now(UTC).isoformat(), run_id, run_id),
                )


def child(stage, operation):
    """Journal a child stage under the inherited run lock; cancellation is shared."""
    context = current_stage.get()
    if context is None:
        return operation()
    store, run_id, parent, attempt = context
    checkpoint()
    return execute(store, run_id, f"{parent}/{attempt}/{stage}", operation)

def checkpoint_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def completed_output(name, input_digest, produce, restore):
    """Reuse a verified result only within the same run and applicable inputs."""
    context = current_stage.get()
    if context is None:
        return produce()
    store, run_id, stage, attempt = context
    checkpoint()
    with closing(store._connect()) as connection:
        records = connection.execute(
            "SELECT * FROM lifecycle_checkpoints WHERE run_id=? AND name=? AND input_digest=? ORDER BY rowid DESC",
            (run_id, name, input_digest),
        ).fetchall()
    for row in records:
        payload = json.loads(row["payload"])
        valid = checkpoint_digest(payload) == row["payload_digest"]
        if valid:
            valid = restore(payload)
        stamp = datetime.now(UTC).isoformat()
        with closing(store._connect()) as connection, connection:
            connection.execute("INSERT INTO lifecycle_recovery VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid4().hex, run_id, stage, attempt, stamp, "RUNNING", "CHECKPOINT_ASSESSED",
                                "VERIFIED" if valid else "INVALID", "NOT_APPLICABLE",
                                "REUSE_COMPLETED_STAGE" if valid else "RESTART_STAGE", stamp))
        if valid:
            return payload
    payload = produce()
    checkpoint()
    if payload is not None:
        with closing(store._connect()) as connection, connection:
            connection.execute("INSERT INTO lifecycle_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (run_id, name, stage, attempt, input_digest, json.dumps(payload), checkpoint_digest(payload)))
    return payload

def record_reconciliation(reason, external_status):
    context = current_stage.get()
    if context is None:
        return
    store, run_id, stage, attempt = context
    stamp = datetime.now(UTC).isoformat()
    with closing(store._connect()) as connection, connection:
        connection.execute("INSERT INTO lifecycle_recovery VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (uuid4().hex, run_id, stage, attempt, stamp, "RUNNING", reason,
                            "NOT_ASSESSED", external_status, "RECONCILE_EXTERNAL_OPERATION", stamp))

def timing_summary(stages):
    """Summarize observed child execution without counting overlap twice."""
    intervals = []
    for row in stages:
        if row["stage"].rsplit("/", 1)[-1] not in {"implement", "write_tests"}:
            continue
        duration = row.get("duration_seconds")
        if duration is not None:
            start = datetime.fromisoformat(row["started_at"]).timestamp()
            intervals.append((start, start + duration, duration))
    if not intervals:
        return {"aggregate_agent_seconds": None, "agent_wall_seconds": None, "agent_overlap_seconds": None}
    aggregate = sum(duration for _, _, duration in intervals)
    wall = 0.0
    end = float("-inf")
    for start, stop, _ in sorted(intervals):
        wall += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return {"aggregate_agent_seconds": aggregate, "agent_wall_seconds": wall,
            "agent_overlap_seconds": max(0.0, aggregate - wall)}
