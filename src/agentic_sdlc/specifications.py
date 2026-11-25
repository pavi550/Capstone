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
                CREATE TABLE IF NOT EXISTS development_cancellations (
                    run_id TEXT PRIMARY KEY REFERENCES specifications(run_id),
                    cancelled_at TEXT NOT NULL
                );
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

    def create(self, draft: dict) -> str:
        digest = specification_digest(draft)
        if digest != draft.get("spec_sha256"):
            raise ValueError("Specification digest mismatch")
        status = draft.get("status")
        if status not in {"AWAITING_APPROVAL", "BLOCKED"}:
            raise ValueError("Only an undecided specification draft can be stored")
        run_id = uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO specifications (run_id, draft, digest, status) VALUES (?, ?, ?, ?)",
                (run_id, json.dumps(draft), digest, status),
            )
            if status == "AWAITING_APPROVAL":
                connection.execute(
                    "INSERT INTO approval_requests VALUES (?, ?)",
                    (run_id, datetime.now(UTC).isoformat()),
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
            last = connection.execute(
                """
                SELECT status FROM development_attempts
                WHERE run_id = ? ORDER BY attempt DESC LIMIT 1
                """, (run_id,),
            ).fetchone()
            if last and last["status"] == "READY_FOR_VERIFICATION":
                raise ValueError("Development is complete; there is no pending attempt to cancel")
            connection.execute(
                "INSERT OR IGNORE INTO development_cancellations VALUES (?, ?)",
                (run_id, datetime.now(UTC).isoformat()),
            )
        return {"run_id": run_id, "status": "CANCELLED"}

    def _claim_development(self, run_id, authority, max_attempts, inputs):
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
            if last:
                previous = json.loads(last["evidence"])
                if previous["inputs"] != inputs:
                    raise ValueError("Resume requires the original source and output directories")
                if last["status"] == "READY_FOR_VERIFICATION":
                    return None, previous
                if last["status"] == "RUNNING":
                    connection.execute(
                        "UPDATE development_attempts SET status = 'INTERRUPTED' WHERE run_id = ? AND attempt = ?",
                        (run_id, last["attempt"]),
                    )
            attempt = last["attempt"] + 1 if last else 1
            if attempt > max_attempts:
                # Preserve an interrupted attempt even when there is no retry budget left.
                return None, {"run_id": run_id, "status": "FAILED", "reason": "Development retry limit reached"}
            connection.execute(
                "INSERT INTO development_attempts VALUES (?, ?, 'RUNNING', ?)",
                (run_id, attempt, json.dumps({"inputs": inputs, "started_at": datetime.now(UTC).isoformat()})),
            )
            return attempt, decision["draft"]

    @staticmethod
    def _cancelled(connection, run_id):
        return connection.execute(
            "SELECT 1 FROM development_cancellations WHERE run_id = ?", (run_id,),
        ).fetchone() is not None

    def develop(
        self, run_id: str, authority: ApprovalAuthority, generate, max_attempts: int, *, inputs: dict,
    ) -> dict:
        """Hold a per-run OS-backed database lock while persisting attempts in the main store."""
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid specification run ID")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError("enterprise.development_attempts must be between 1 and 5")
        lock_path = self.path.with_name(f".{self.path.name}.{run_id}.lock")
        with closing(sqlite3.connect(lock_path, timeout=0)) as lock:
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                raise ValueError("Development run is locked or the lock database is unavailable") from None
            attempt, data = self._claim_development(run_id, authority, max_attempts, inputs)
            if attempt is None:
                return data
            started = perf_counter()
            try:
                evidence = generate(data, attempt)
            except (OSError, RuntimeError, ValueError) as exc:
                evidence = {"run_id": run_id, "attempt": attempt, "status": "FAILED",
                            "failure_type": type(exc).__name__}
            evidence["inputs"] = inputs
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
                SELECT evidence FROM development_attempts
                WHERE run_id = ? AND status = 'READY_FOR_VERIFICATION'
                ORDER BY attempt DESC LIMIT 1
                """, (run_id,),
            ).fetchone()
            if attempt is None:
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

    def record_receipt(self, run_id: str, kind: str, record: dict) -> dict:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
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
