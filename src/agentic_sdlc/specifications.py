"""Transactional storage for immutable specification drafts and operator decisions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from .approval import ApprovalAuthority


def specification_digest(draft: dict) -> str:
    payload = {key: value for key, value in draft.items() if key != "spec_sha256"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


class SpecificationStore:
    """Local operator storage. Authenticate operator access outside this class."""

    def __init__(self, path: Path):
        self.path = path
        with closing(self._connect()) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS specifications (
                    run_id TEXT PRIMARY KEY,
                    draft TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('AWAITING_APPROVAL', 'BLOCKED', 'APPROVED', 'REJECTED')
                    ),
                    actor TEXT,
                    decided_at TEXT,
                    reason TEXT
                )
            """)

            connection.executescript("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    run_id TEXT PRIMARY KEY REFERENCES specifications(run_id),
                    requested_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_signatures (
                    run_id TEXT PRIMARY KEY REFERENCES specifications(run_id),
                    signature TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    kind TEXT NOT NULL,
                    record TEXT NOT NULL,
                    PRIMARY KEY (run_id, kind)
                );
                CREATE TABLE IF NOT EXISTS provider_requests (
                    request_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    stage TEXT NOT NULL, attempt INTEGER NOT NULL, agent TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL,
                    charged_input INTEGER NOT NULL, charged_output INTEGER NOT NULL, charged_cost INTEGER,
                    actual_input INTEGER, actual_output INTEGER, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_request_ownership (
                    request_id TEXT PRIMARY KEY REFERENCES provider_requests(request_id),
                    stage TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    experiment_id TEXT NOT NULL, trial_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE REFERENCES specifications(run_id),
                    PRIMARY KEY (experiment_id, trial_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_execution_policy (
                    experiment_id TEXT PRIMARY KEY, digest TEXT NOT NULL, record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ci_submissions (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    correlation TEXT NOT NULL, state TEXT NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY (run_id, correlation)
                );
                CREATE TABLE IF NOT EXISTS unified_runs (
                    run_id TEXT PRIMARY KEY REFERENCES specifications(run_id),
                    plan TEXT NOT NULL, progress TEXT
                );
                CREATE TABLE IF NOT EXISTS provider_usage (
                    request_id TEXT PRIMARY KEY REFERENCES provider_requests(request_id),
                    record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_stages (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    state TEXT NOT NULL,
                    failure TEXT,
                    refs TEXT,
                    PRIMARY KEY (run_id, stage, attempt)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_recovery (
                    recovery_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    stage TEXT NOT NULL, attempt INTEGER NOT NULL,
                    detected_at TEXT NOT NULL, previous_status TEXT NOT NULL,
                    reason TEXT NOT NULL, artifact_integrity TEXT NOT NULL,
                    external_status TEXT NOT NULL, action TEXT NOT NULL,
                    decided_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_recovery_timing (
                    recovery_id TEXT PRIMARY KEY REFERENCES lifecycle_recovery(recovery_id),
                    assessment_seconds REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_intervals (
                    run_id TEXT NOT NULL, stage TEXT NOT NULL, attempt INTEGER NOT NULL,
                    worker_id TEXT NOT NULL, heartbeat_at TEXT NOT NULL, duration_seconds REAL,
                    PRIMARY KEY (run_id, stage, attempt)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_checkpoints (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    name TEXT NOT NULL, stage TEXT NOT NULL, attempt INTEGER NOT NULL,
                    input_digest TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    PRIMARY KEY (run_id, name, stage, attempt)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_stage_cancellations (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    stage TEXT NOT NULL, requested_at TEXT NOT NULL,
                    requester TEXT, reason TEXT, completed_at TEXT,
                    PRIMARY KEY (run_id, stage)
                );
                CREATE TABLE IF NOT EXISTS intake_invocations (
                    run_id TEXT PRIMARY KEY REFERENCES specifications(run_id),
                    input_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_containers (
                    name TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    stage TEXT NOT NULL, attempt INTEGER NOT NULL, host TEXT NOT NULL,
                    cleanup_state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intake_failures (
                    delivery_id TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    failed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intake_events (
                    delivery_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL,
                    issue TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    run_id TEXT REFERENCES specifications(run_id)
                );
                CREATE TABLE IF NOT EXISTS receipt_history (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    attempt INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    record TEXT NOT NULL,
                    PRIMARY KEY (run_id, attempt, kind)
                );
                CREATE TABLE IF NOT EXISTS development_cancellations (
                    run_id TEXT PRIMARY KEY REFERENCES specifications(run_id),
                    cancelled_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS human_activities (
                    activity_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    actor TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    state TEXT NOT NULL,
                    note TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_human_activity
                    ON human_activities(actor) WHERE state = 'ACTIVE';
                CREATE TABLE IF NOT EXISTS development_attempts (
                    run_id TEXT NOT NULL REFERENCES specifications(run_id),
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT,
                    PRIMARY KEY (run_id, attempt)
                );
            """)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def begin_intake(self, issue, *, delivery_id=None):
        draft = {"issue": issue, "status": "BLOCKED", "intake_pending": True,
                 "acceptance_criteria": []}
        draft["spec_sha256"] = specification_digest(draft)
        return self.create(draft, delivery_id=delivery_id)

    def finish_intake(self, run_id, draft):
        digest = specification_digest(draft)
        if draft.get("spec_sha256") != digest or draft.get("status") not in {"BLOCKED", "AWAITING_APPROVAL"}:
            raise ValueError("Invalid completed intake draft")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get(connection, run_id)
            if self._cancelled(connection, run_id):
                raise ValueError("Intake was cancelled")
            if not existing["draft"].get("intake_pending") or existing["actor"]:
                raise ValueError("Intake cannot replace an existing specification")
            if existing["draft"]["issue"] != draft["issue"]:
                raise ValueError("Intake issue identity changed")
            connection.execute("UPDATE specifications SET draft=?, digest=?, status=? WHERE run_id=?",
                               (json.dumps(draft), digest, draft["status"], run_id))
            connection.execute("UPDATE intake_events SET status='DRAFTED' WHERE run_id=?", (run_id,))
            if draft["status"] == "AWAITING_APPROVAL":
                connection.execute("INSERT INTO approval_requests VALUES (?, ?)",
                                   (run_id, datetime.now(UTC).isoformat()))
        return run_id

    def create(self, draft: dict, *, delivery_id=None, trial=None) -> str:
        digest = specification_digest(draft)
        if digest != draft.get("spec_sha256"):
            raise ValueError("Specification digest mismatch")
        status = draft.get("status")
        if status not in {"AWAITING_APPROVAL", "BLOCKED"}:
            raise ValueError("Only an undecided specification draft can be stored")
        run_id = uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if trial is not None:
                existing = connection.execute(
                    "SELECT run_id FROM experiment_runs WHERE experiment_id=? AND trial_id=?", trial,
                ).fetchone()
                if existing:
                    return existing["run_id"]
            if delivery_id is not None:
                event = connection.execute(
                    "SELECT issue, run_id FROM intake_events WHERE delivery_id = ?", (delivery_id,),
                ).fetchone()
                if not event or event["issue"] != draft["issue"]:
                    raise ValueError("Intake delivery does not identify this Jira issue")
                if event["run_id"]:
                    return event["run_id"]
            connection.execute(
                "INSERT INTO specifications (run_id, draft, digest, status) VALUES (?, ?, ?, ?)",
                (run_id, json.dumps(draft), digest, status),
            )
            if trial is not None:
                connection.execute("INSERT INTO experiment_runs VALUES (?, ?, ?)", (*trial, run_id))
            if status == "AWAITING_APPROVAL":
                connection.execute(
                    "INSERT INTO approval_requests VALUES (?, ?)",
                    (run_id, datetime.now(UTC).isoformat()),
                )
            if delivery_id is not None:
                connection.execute(
                    "UPDATE intake_events SET run_id = ?, status = ? WHERE delivery_id = ?",
                    (run_id, "QUEUED" if draft.get("intake_pending") else "DRAFTED", delivery_id),
                )
        return run_id

    @staticmethod
    def _get(connection, run_id):
        row = connection.execute(
            "SELECT * FROM specifications WHERE run_id = ?", (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Specification run not found")
        result = dict(row)
        draft = json.loads(result["draft"])
        if draft.get("spec_sha256") != result["digest"] or specification_digest(draft) != result["digest"]:
            raise ValueError("Stored specification digest mismatch")
        result["draft"] = draft
        return result

    def get(self, run_id: str) -> dict:
        with closing(self._connect()) as connection:
            result = self._get(connection, run_id)
            attempts = connection.execute(
                "SELECT attempt, status, evidence FROM development_attempts WHERE run_id = ? ORDER BY attempt",
                (run_id,),
            )
            result["development_attempts"] = [
                {**dict(row), "evidence": json.loads(row["evidence"]) if row["evidence"] else None}
                for row in attempts
            ]
            cancelled = connection.execute(
                "SELECT cancelled_at FROM development_cancellations WHERE run_id = ?", (run_id,),
            ).fetchone()
            result["cancelled_at"] = cancelled["cancelled_at"] if cancelled else None
            request = connection.execute(
                "SELECT requested_at FROM approval_requests WHERE run_id = ?", (run_id,),
            ).fetchone()
            result["approval_requested_at"] = request["requested_at"] if request else None
            result["approval_wait_seconds"] = None
            if request and result["decided_at"]:
                elapsed = datetime.fromisoformat(result["decided_at"]) - datetime.fromisoformat(
                    request["requested_at"],
                )
                result["approval_wait_seconds"] = max(0, elapsed.total_seconds())
            return result

    def enqueue_issue(self, delivery_id, digest, issue):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO intake_events VALUES (?, ?, ?, 'QUEUED', ?, NULL)",
                (delivery_id, digest, issue, datetime.now(UTC).isoformat()),
            )
            event = connection.execute("SELECT * FROM intake_events WHERE delivery_id = ?", (delivery_id,)).fetchone()
            if event["digest"] != digest or event["issue"] != issue:
                raise ValueError("Delivery ID was reused with different intake data")
            return dict(event)

    def fail_intake(self, delivery_id, failure, limit):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM intake_events WHERE delivery_id=?", (delivery_id,)).fetchone()
            if not row or row["status"] != "QUEUED":
                return
            connection.execute(
                "INSERT INTO intake_failures VALUES (?,1,?,?) ON CONFLICT(delivery_id) DO UPDATE SET "
                "attempts=attempts+1,last_error=excluded.last_error,failed_at=excluded.failed_at",
                (delivery_id, failure, datetime.now(UTC).isoformat()),
            )
            connection.execute(
                "UPDATE intake_events SET status='DEAD_LETTER' WHERE delivery_id=? AND "
                "(SELECT attempts FROM intake_failures WHERE delivery_id=?)>=?", (delivery_id, delivery_id, limit),
            )

    def dead_letters(self):
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT intake_events.delivery_id,issue,attempts,last_error,failed_at FROM intake_events "
                "JOIN intake_failures USING(delivery_id) WHERE status='DEAD_LETTER' ORDER BY failed_at",
            )]

    def intake_queue(self):
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM intake_events WHERE status = 'QUEUED' ORDER BY received_at, delivery_id",
            )]

    def resolve_issue(self, issue: str) -> str:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT run_id FROM specifications WHERE json_extract(draft, '$.issue') = ? LIMIT 2",
                (issue,),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("Issue must identify exactly one stored run; use --run-id for multiple drafts")
        return rows[0]["run_id"]

    def human_activity(self, run_id, actor, kind, *, end=False, interrupt=False, note=None):
        kinds = {"requirements", "review", "re-review", "manual-code", "manual-test", "rework", "validation"}
        if not actor.strip() or len(actor) > 128 or kind not in kinds or (note and len(note) > 2000):
            raise ValueError("Supply an actor, a supported human activity and a bounded note")
        stamp = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get(connection, run_id)
            row = connection.execute(
                "SELECT * FROM human_activities WHERE actor = ? AND state = 'ACTIVE'", (actor,),
            ).fetchone()
            if end:
                if not row or (row["run_id"], row["kind"]) != (run_id, kind):
                    raise ValueError("No matching active human activity")
                identity = row["activity_id"]
                connection.execute(
                    "UPDATE human_activities SET ended_at = ?, state = ?, note = ? WHERE activity_id = ?",
                    (stamp, "INTERRUPTED" if interrupt else "COMPLETE", note, identity),
                )
            elif row:
                if (row["run_id"], row["kind"]) != (run_id, kind):
                    raise ValueError("Finish or interrupt the actor's existing activity first")
                identity = row["activity_id"]
            else:
                identity = uuid4().hex
                connection.execute(
                    "INSERT INTO human_activities VALUES (?, ?, ?, ?, ?, NULL, 'ACTIVE', NULL)",
                    (identity, run_id, actor, kind, stamp),
                )
            return dict(connection.execute(
                "SELECT * FROM human_activities WHERE activity_id = ?", (identity,),
            ).fetchone())

    def human_effort(self, run_id):
        with closing(self._connect()) as connection:
            self._get(connection, run_id)
            rows = connection.execute(
                "SELECT * FROM human_activities WHERE run_id = ? ORDER BY started_at", (run_id,),
            ).fetchall()
        sessions, totals = [], {}
        for row in rows:
            session = dict(row)
            duration = None
            if row["state"] == "COMPLETE":
                elapsed = datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])
                if elapsed.total_seconds() >= 0:
                    duration = elapsed.total_seconds()
                    totals[row["kind"]] = totals.get(row["kind"], 0) + duration
            sessions.append({**session, "active_seconds": duration})
        return {"sessions": sessions, "measured_seconds_by_kind": totals,
                "measured_human_seconds": sum(totals.values()) if totals else None,
                "incomplete_sessions": sum(session["active_seconds"] is None for session in sessions)}

    def decide(
        self, run_id: str, digest: str, status: str, actor: str, reason: str | None,
        authority: ApprovalAuthority | None = None,
    ) -> dict:
        actor = actor.strip()
        reason = reason.strip() if reason else None
        if status not in {"APPROVED", "REJECTED"} or not actor:
            raise ValueError("A valid decision and an operator identifier are required")
        if status == "REJECTED" and not reason:
            raise ValueError("A rejection reason is required")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._get(connection, run_id)
            if current["digest"] != digest:
                raise ValueError("Approval digest does not match the stored specification")
            if (current["status"], current["actor"], current["reason"]) == (status, actor, reason):
                if authority is not None:
                    signed = connection.execute(
                        "SELECT signature FROM decision_signatures WHERE run_id = ?", (run_id,),
                    ).fetchone()
                    if not signed or not authority.verify(run_id, current, signed["signature"]):
                        raise ValueError("This decision was not authenticated by the configured authority")
                return current
            if current["status"] != "AWAITING_APPROVAL":
                raise ValueError("This specification is not awaiting approval")
            connection.execute(
                "UPDATE specifications SET status = ?, actor = ?, reason = ?, decided_at = ? WHERE run_id = ?",
                (status, actor, reason, datetime.now(UTC).isoformat(), run_id),
            )
            decision = self._get(connection, run_id)
            if authority is not None:
                self._save_signature(connection, run_id, decision, authority)
            return decision


    @staticmethod
    def _save_signature(connection, run_id, decision, authority):
        connection.execute(
            "INSERT OR IGNORE INTO decision_signatures (run_id, signature) VALUES (?, ?)",
            (run_id, authority.sign(run_id, decision)),
        )

    def cancel(self, run_id: str) -> dict:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get(connection, run_id)
            if connection.execute(
                "SELECT 1 FROM receipts WHERE run_id=? AND kind IN ('validation', 'validation_v2')", (run_id,),
            ).fetchone():
                raise ValueError("A finalized run cannot be cancelled")
            active = connection.execute(
                "SELECT 1 FROM lifecycle_stages WHERE run_id=? AND state='RUNNING'", (run_id,),
            ).fetchone()
            connection.execute(
                "INSERT OR IGNORE INTO development_cancellations VALUES (?, ?)",
                (run_id, datetime.now(UTC).isoformat()),
            )
        return {"run_id": run_id, "status": "CANCELLING" if active else "CANCELLED"}

    def _claim_development(self, run_id, authority, max_attempts, inputs, correction, correction_limit):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            decision = self._get(connection, run_id)
            if self._cancelled(connection, run_id):
                raise ValueError("Development was cancelled")
            signed = connection.execute(
                "SELECT signature FROM decision_signatures WHERE run_id = ?", (run_id,),
            ).fetchone()
            if decision["status"] != "APPROVED" or not authority.verify(
                run_id, decision, signed["signature"] if signed else None,
            ):
                raise ValueError("A verified local approval for the exact specification is required")
            last = connection.execute(
                """
                SELECT attempt, status, evidence FROM development_attempts
                WHERE run_id = ? ORDER BY attempt DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            remediation = None
            if correction and (not last or last["status"] != "READY_FOR_VERIFICATION"):
                raise ValueError("Resume the current attempt before requesting another correction")
            if last:
                previous = json.loads(last["evidence"])
                remediation = previous.get("remediation")
                if previous["inputs"] != inputs:
                    raise ValueError("Resume requires the original source and output directories")
                if last["status"] == "READY_FOR_VERIFICATION":
                    if correction is None:
                        return None, previous
                    if previous.get("manifest_sha256") != correction["manifest_sha256"]:
                        raise ValueError("Correction refers to stale development evidence")
                    if connection.execute(
                        "SELECT 1 FROM receipts WHERE run_id = ? AND kind IN ('review_approval', 'validation')",
                        (run_id,),
                    ).fetchone():
                        raise ValueError("A run with final review approval cannot be corrected in place")
                    cycle = (remediation or {}).get("cycle", 0) + 1
                    if cycle > correction_limit:
                        return None, {"run_id": run_id, "status": "FAILED", "reason": "Correction limit reached"}
                    remediation = {**correction, "cycle": cycle}
                    connection.execute(
                        "INSERT INTO receipt_history SELECT run_id, ?, kind, record FROM receipts WHERE run_id = ?",
                        (last["attempt"], run_id),
                    )
                    connection.execute("DELETE FROM receipts WHERE run_id = ?", (run_id,))
                if last["status"] == "RUNNING":
                    connection.execute(
                        "UPDATE development_attempts SET status = 'INTERRUPTED' WHERE run_id = ? AND attempt = ?",
                        (run_id, last["attempt"]),
                    )
            attempt = last["attempt"] + 1 if last else 1
            retry_start = remediation["source_attempt"] + 1 if remediation else 1
            if attempt - retry_start >= max_attempts:
                # Preserve an interrupted attempt even when there is no retry budget left.
                return None, {"run_id": run_id, "status": "FAILED", "reason": "Development retry limit reached"}
            connection.execute(
                "INSERT INTO development_attempts VALUES (?, ?, 'RUNNING', ?)",
                (run_id, attempt, json.dumps({"inputs": inputs, "started_at": datetime.now(UTC).isoformat(), "remediation": remediation})),
            )
            return attempt, (decision["draft"], remediation)

    @staticmethod
    def _cancelled(connection, run_id):
        return connection.execute(
            "SELECT 1 FROM development_cancellations WHERE run_id = ?", (run_id,),
        ).fetchone() is not None

    def develop(
        self, run_id: str, authority: ApprovalAuthority, generate, max_attempts: int, *, inputs: dict,
        correction=None, correction_limit=0, remediation_generate=None,
    ) -> dict:
        """Hold a per-run OS-backed database lock while persisting attempts in the main store."""
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid specification run ID")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError("enterprise.development_attempts must be between 1 and 5")
        if type(correction_limit) is not int or not 0 <= correction_limit <= 5:
            raise ValueError("enterprise.correction_cycles must be between 0 and 5")
        lock_path = self.path.with_name(f".{self.path.name}.{run_id}.lock")
        with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                raise ValueError("Development run is locked or the lock database is unavailable") from None
            attempt, data = self._claim_development(run_id, authority, max_attempts, inputs, correction, correction_limit)
            if attempt is None:
                return data
            draft, remediation = data
            started = perf_counter()
            try:
                if remediation and remediation_generate is None:
                    raise ValueError("Correction requires the configured remediation generator")
                evidence = remediation_generate(draft, attempt, remediation) if remediation else generate(draft, attempt)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                evidence = {"run_id": run_id, "attempt": attempt, "status": "FAILED",
                            "failure_type": type(exc).__name__}
            evidence["inputs"] = inputs
            if remediation:
                evidence["remediation"] = remediation
            evidence["duration_ms"] = round((perf_counter() - started) * 1000, 3)
            evidence["finished_at"] = datetime.now(UTC).isoformat()
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if self._cancelled(connection, run_id):
                    evidence["status"] = "CANCELLED"
                previous = connection.execute(
                    "SELECT evidence FROM development_attempts WHERE run_id = ? AND attempt = ?",
                    (run_id, attempt),
                ).fetchone()
                evidence["started_at"] = json.loads(previous["evidence"])["started_at"]
                connection.execute(
                    "UPDATE development_attempts SET status = ?, evidence = ? WHERE run_id = ? AND attempt = ?",
                    (evidence["status"], json.dumps(evidence), run_id, attempt),
                )
            return evidence


    def approved_specification(self, run_id, authority):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            decision = self._get(connection, run_id)
            if self._cancelled(connection, run_id):
                raise ValueError("Development was cancelled")
            signed = connection.execute(
                "SELECT signature FROM decision_signatures WHERE run_id = ?", (run_id,),
            ).fetchone()
            if decision["status"] != "APPROVED" or not authority.verify(
                run_id, decision, signed["signature"] if signed else None,
            ):
                raise ValueError("A verified requirement approval is required")
            return decision["draft"]

    def verified_development(self, run_id: str, authority: ApprovalAuthority) -> tuple[dict, dict]:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            decision = self._get(connection, run_id)
            signed = connection.execute(
                "SELECT signature FROM decision_signatures WHERE run_id = ?", (run_id,),
            ).fetchone()
            if decision["status"] != "APPROVED" or not authority.verify(
                run_id, decision, signed["signature"] if signed else None,
            ):
                raise ValueError("A verified requirement approval is required")
            attempt = connection.execute(
                """
                SELECT evidence, status FROM development_attempts
                WHERE run_id = ?
                ORDER BY attempt DESC LIMIT 1
                """, (run_id,),
            ).fetchone()
            if attempt is None or attempt["status"] != "READY_FOR_VERIFICATION":
                raise ValueError("No integrated development artifacts are ready")
            evidence = json.loads(attempt["evidence"])
            if evidence["spec_sha256"] != decision["digest"]:
                raise ValueError("Development evidence is bound to a different specification")
            return decision, evidence

    def receipt(self, run_id: str, kind: str, *, optional: bool = False) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record FROM receipts WHERE run_id = ? AND kind = ?", (run_id, kind),
            ).fetchone()
            if row is None:
                if optional:
                    return None
                raise ValueError(f"Missing {kind} receipt")
            return json.loads(row["record"])

    def provider_requests(self, run_id):
        with closing(self._connect()) as connection:
            return [{**dict(row), "normalized_usage": json.loads(row["normalized_usage"] or "null")}
                    for row in connection.execute(
                        "SELECT requests.*, usage.record AS normalized_usage FROM provider_requests requests "
                        "LEFT JOIN provider_usage usage USING(request_id) WHERE requests.run_id=? ORDER BY requests.rowid",
                        (run_id,),
                    )]

    def lifecycle_stages(self, run_id):
        with closing(self._connect()) as connection:
            return [{**dict(row), "refs": json.loads(row["refs"] or "null")} for row in connection.execute(
                "SELECT stages.*, intervals.worker_id, intervals.heartbeat_at, intervals.duration_seconds "
                "FROM lifecycle_stages stages LEFT JOIN lifecycle_intervals intervals USING(run_id, stage, attempt) "
                "WHERE run_id=? ORDER BY started_at", (run_id,),
            )]

    def historical_receipts(self, run_id):
        with closing(self._connect()) as connection:
            return [{**dict(row), "record": json.loads(row["record"])} for row in connection.execute(
                "SELECT attempt, kind, record FROM receipt_history WHERE run_id = ? ORDER BY attempt, kind", (run_id,),
            )]

    def record_receipt(self, run_id: str, kind: str, record: dict, *, manifest=None) -> dict:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._cancelled(connection, run_id):
                from .lifecycle import StageCancelled

                raise StageCancelled("Cancelled runs cannot publish new receipts")
            manifest = manifest or record.get("payload", record).get("manifest_sha256")
            if manifest:
                active = connection.execute(
                    "SELECT status, evidence FROM development_attempts WHERE run_id = ? ORDER BY attempt DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
                if (not active or active["status"] != "READY_FOR_VERIFICATION"
                        or json.loads(active["evidence"]).get("manifest_sha256") != manifest):
                    raise ValueError("Receipt refers to an obsolete development attempt")
            opposite_decisions = {
                "development_approval": "development_rejection",
                "development_rejection": "development_approval",
                "quality_approval": "quality_rejection",
                "quality_rejection": "quality_approval",
                "review_approval": "review_rejection",
                "review_rejection": "review_approval",
            }
            if kind in opposite_decisions:
                opposite = opposite_decisions[kind]
                if connection.execute(
                    "SELECT 1 FROM receipts WHERE run_id = ? AND kind = ?", (run_id, opposite),
                ).fetchone():
                    raise ValueError("An incompatible human decision is already recorded")
            previous = connection.execute(
                "SELECT record FROM receipts WHERE run_id = ? AND kind = ?", (run_id, kind),
            ).fetchone()
            if previous:
                saved = json.loads(previous["record"])
                if saved != record:
                    raise ValueError(f"A different {kind} receipt is already recorded")
                return saved
            connection.execute(
                "INSERT INTO receipts VALUES (?, ?, ?)", (run_id, kind, json.dumps(record)),
            )
        return record
