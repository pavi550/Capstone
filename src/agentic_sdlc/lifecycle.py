"""Persistent stage journal and cooperative cancellation for stored enterprise runs."""

import json
import re
import sqlite3
from contextlib import closing, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime

current_stage = ContextVar("enterprise_stage", default=None)


class StageCancelled(RuntimeError):
    pass


def checkpoint():
    context = current_stage.get()
    if context:
        store, run_id, _, _ = context
        with closing(store._connect()) as connection:
            if store._cancelled(connection, run_id):
                raise StageCancelled("Enterprise run cancelled")


def execute(store, run_id, stage, operation):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid lifecycle run ID")
    parent = current_stage.get()
    nested = parent is not None and parent[0].path == store.path and parent[1] == run_id
    lock_path = store.path.with_name(f".{store.path.name}.{run_id}.lifecycle.lock")
    with (nullcontext(None) if nested else closing(sqlite3.connect(lock_path, timeout=0))) as lock:
        try:
            if lock is not None:
                lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            raise ValueError("Another lifecycle stage is active for this run") from None
        with closing(store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            store._get(connection, run_id)
            if store._cancelled(connection, run_id):
                raise StageCancelled("Enterprise run cancelled")
            stamp = datetime.now(UTC).isoformat()
            if not nested:
                connection.execute(
                    "UPDATE lifecycle_stages SET state='INTERRUPTED', finished_at=?, failure='PROCESS_INTERRUPTED' "
                    "WHERE run_id=? AND state='RUNNING'", (stamp, run_id),
                )
            attempt = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0)+1 FROM lifecycle_stages WHERE run_id=? AND stage=?",
                (run_id, stage),
            ).fetchone()[0]
            connection.execute("INSERT INTO lifecycle_stages VALUES (?, ?, ?, ?, NULL, 'RUNNING', NULL, NULL)",
                               (run_id, stage, attempt, stamp))
        token = current_stage.set((store, run_id, stage, attempt))
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
            if isinstance(exc, StageCancelled):
                status = "CANCELLED"
            elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
                status = "INTERRUPTED"
            raise
        finally:
            current_stage.reset(token)
            with closing(store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if store._cancelled(connection, run_id):
                    status = "CANCELLED"
                connection.execute(
                    "UPDATE lifecycle_stages SET finished_at=?, state=?, failure=?, refs=? "
                    "WHERE run_id=? AND stage=? AND attempt=?",
                    (datetime.now(UTC).isoformat(), status, failure, json.dumps(references), run_id, stage, attempt),
                )


def child(stage, operation):
    """Journal a child stage under the inherited run lock; cancellation is shared."""
    context = current_stage.get()
    if context is None:
        return operation()
    store, run_id, parent, attempt = context
    checkpoint()
    return execute(store, run_id, f"{parent}/{attempt}/{stage}", operation)
