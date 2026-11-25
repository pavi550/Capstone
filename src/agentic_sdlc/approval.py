"""Local operator authentication for digest-bound decisions."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Protocol


class ApprovalAuthority(Protocol):
    def sign(self, run_id: str, decision: dict) -> str: ...

    def verify(self, run_id: str, decision: dict, signature: str | None) -> bool: ...


class LocalApprovalAuthority:
    """Authenticate possession of an operator-specific key on a trusted local host."""

    def __init__(self, config):
        self._config = config

    def _key(self, role: str, actor: str) -> str:
        identities = self._config.get(f"enterprise.{role}", {})
        if not isinstance(identities, dict) or actor not in identities or "." in actor:
            raise ValueError("Actor has no configured local identity for this role")
        key = self._config.secret(f"enterprise.{role}.{actor}")
        if key is None or len(key.encode()) < 32:
            raise ValueError("The selected local identity requires a key of at least 32 bytes")
        return key

    def require_separation(self, verifier: str, approver: str) -> None:
        if verifier == approver or hmac.compare_digest(
            self._key("verifiers", verifier).encode(), self._key("approvers", approver).encode(),
        ):
            raise ValueError("Verification requires a distinct local verifier identity and key")

    def seal(self, role: str, purpose: str, actor: str, payload: dict) -> str:
        message = json.dumps(
            [purpose, actor, payload], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()
        return hmac.new(self._key(role, actor).encode(), message, hashlib.sha256).hexdigest()

    def validate_seal(self, role: str, purpose: str, actor: str, payload: dict, signature: str) -> bool:
        return hmac.compare_digest(self.seal(role, purpose, actor, payload), signature)

    def sign(self, run_id: str, decision: dict) -> str:
        record = {name: decision[name] for name in ("digest", "status", "reason", "decided_at")}
        return self.seal("approvers", "requirements", decision["actor"], {"run_id": run_id, **record})

    def verify(self, run_id: str, decision: dict, signature: str | None) -> bool:
        return signature is not None and hmac.compare_digest(self.sign(run_id, decision), signature)
