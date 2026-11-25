"""Bind independent local attestations and final operator approval to exact artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .agents.review import ReviewAgent
from .config import Config, _deep_merge
from .development import DevelopmentGuardrails, validate_provider, verify_artifact
from .llm import make_llm
from .state import Requirement, Work

CHECKS = {"acceptance", "regression", "security", "guardrails", "definition_of_done", "dev"}


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _artifact(store, run_id, authority):
    decision, evidence = store.verified_development(run_id, authority)
    verify_artifact(evidence)
    identity = {
        "run_id": run_id, "spec_sha256": evidence["spec_sha256"],
        "manifest_sha256": evidence["manifest_sha256"],
    }
    return decision, evidence, identity


def record_verification(store, run_id, authority, actor, result):
    decision, evidence, identity = _artifact(store, run_id, authority)
    if evidence["execution_mode"] != "OFFLINE_STUB":
        raise ValueError("Synthetic verification cannot satisfy a configured model run")
    authority.require_separation(actor, decision["actor"])
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in identity.items()):
        raise ValueError("Verification result is bound to a different run, specification or manifest")
    checks = result.get("checks")
    if (not isinstance(checks, dict) or set(checks) != CHECKS
            or not all(type(value) is bool for value in checks.values())):
        raise ValueError("Provide all mandatory check outcomes as booleans")
    if result.get("environment") != "OFFLINE_SIMULATION":
        raise ValueError("This workflow supports only attested OFFLINE_SIMULATION verification")
    payload = {
        **identity, "environment": "OFFLINE_SIMULATION", "checks": checks,
        "status": "PASSED" if all(checks.values()) else "BLOCKED",
    }
    signature = authority.seal("verifiers", "verification", actor, payload)
    store.record_receipt(run_id, "verification", {"actor": actor, "payload": payload, "mac": signature})
    return payload


def execute_verification(store, run_id, authority, actor, config, acceptance):
    from .container_verifier import DockerVerifier

    decision, evidence, identity = _artifact(store, run_id, authority)
    authority.require_separation(actor, decision["actor"])
    existing = store.receipt(run_id, "actual_verification", optional=True)
    if existing:
        return _read_verification(
            store, run_id, authority, decision, identity, evidence, allow_blocked=True,
        )["payload"]
    root = Path(evidence["artifact_dir"])
    acceptance = acceptance.resolve()
    if acceptance.is_relative_to(root.parent) or store.path.resolve().is_relative_to(acceptance):
        raise ValueError("Keep acceptance inputs outside generated outputs and the approval store")
    expected = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["integrated"]
    checks = DockerVerifier(config).run(
        root / "integrated", acceptance, {**identity, "attempt": evidence["attempt"],
                                        "executor_identity": actor}, expected,
    )
    verify_artifact(evidence)
    payload = {
        **identity, "environment": "LOCAL_ACTUAL", "checks": checks,
        "status": "PASSED" if all(check["result"] == "PASSED" for check in checks) else "BLOCKED",
    }
    signature = authority.seal("verifiers", "actual_verification", actor, payload)
    store.record_receipt(run_id, "actual_verification", {"actor": actor, "payload": payload, "mac": signature})
    return payload


def _read_verification(store, run_id, authority, decision, identity, evidence, *, allow_blocked=False):
    receipt = store.receipt(run_id, "actual_verification", optional=True)
    purpose = "actual_verification"
    if receipt is None:
        if evidence["execution_mode"] != "OFFLINE_STUB":
            raise ValueError("Configured model runs require actual verification receipts")
        purpose = "verification"
        receipt = store.receipt(run_id, purpose)
    payload = receipt["payload"]
    authority.require_separation(receipt["actor"], decision["actor"])
    if (any(payload.get(key) != value for key, value in identity.items())
            or not authority.validate_seal("verifiers", purpose, receipt["actor"], payload, receipt["mac"])):
        raise ValueError("Verification receipt is stale or unauthenticated")
    if purpose == "actual_verification":
        from .container_verifier import CHECK_IDS

        checks = payload.get("checks")
        if (payload.get("environment") != "LOCAL_ACTUAL" or not isinstance(checks, list)
                or len(checks) != len(CHECK_IDS) or {check["check_id"] for check in checks} != CHECK_IDS
                or any(check.get("execution_mode") != "LOCAL_ACTUAL"
                       or check.get("attempt") != evidence["attempt"]
                       or check.get("executor_identity") != receipt["actor"]
                       or any(check.get(key) != value for key, value in identity.items()) for check in checks)):
            raise ValueError("Missing or mismatched actual verification checks")
        passed = all(check.get("result") == "PASSED" and check.get("exit_code") == 0 for check in checks)
        if payload["status"] != ("PASSED" if passed else "BLOCKED"):
            raise ValueError("Verification outcome disagrees with the check receipts")
    if payload["status"] != "PASSED" and not allow_blocked:
        raise ValueError("Independent verification failed a mandatory gate")
    return receipt


def review(store, run_id, authority, config, *, allow_provider=False):
    decision, evidence, identity = _artifact(store, run_id, authority)
    verification = _read_verification(store, run_id, authority, decision, identity, evidence)
    previous = store.receipt(run_id, "review", optional=True)
    if previous:
        _, existing = _review(store, run_id, authority, decision, identity, evidence, allow_blocked=True)
        return {**existing, "review_sha256": _digest(existing)}
    draft = decision["draft"]
    requirement = Requirement(
        draft["issue"], draft["title"], draft["description"],
        [item["text"] for item in draft["acceptance_criteria"]],
    )
    work = Work(requirement, files=evidence["code_files"], test_files=evidence["test_files"])
    safe_config = Config(_deep_merge(config.data, {"review": {"external_tools": "none"}}))
    real_model = evidence["execution_mode"] == "CONFIGURED_LOCAL_LLM"
    if real_model:
        inputs = evidence["inputs"]
        if any(config.get(f"llm.{setting}") != inputs[key]
               for setting, key in (("provider", "provider"), ("model", "model"), ("base_url", "endpoint"))):
            raise ValueError("Review requires the same configured provider and model as development")
    else:
        safe_config = Config(_deep_merge(safe_config.data, {"llm": {"provider": "stub"}}))
    validate_provider(safe_config, allow_provider)
    llm = make_llm(safe_config)
    agent = ReviewAgent(llm, DevelopmentGuardrails(safe_config), safe_config)
    root = Path(evidence["artifact_dir"]) / "integrated"
    findings = agent.security(root, work)
    pattern, trace = agent.pattern(root, work)
    findings.extend(pattern)
    findings.extend(agent.llm_vote(root, work))
    payload = {
        **identity, "verification_sha256": _digest(verification),
        "status": "BLOCKED" if any(f.severity in {"critical", "high"} for f in findings)
        else "WAITING_REVIEW_APPROVAL",
        "trace": trace, "findings": [finding.to_dict() for finding in findings],
        "provider_usage": llm.usage() if real_model else [],
    }
    store.record_receipt(run_id, "review", payload)
    return {**payload, "review_sha256": _digest(payload)}


def _review(store, run_id, authority, decision, identity, evidence, *, allow_blocked=False):
    verification = _read_verification(store, run_id, authority, decision, identity, evidence)
    bundle = store.receipt(run_id, "review")
    if (any(bundle.get(key) != value for key, value in identity.items())
            or bundle["verification_sha256"] != _digest(verification)):
        raise ValueError("Review refers to stale verification evidence")
    if bundle["status"] != "WAITING_REVIEW_APPROVAL" and not allow_blocked:
        raise ValueError("Review found a blocking issue")
    return verification, bundle


def approve_review(store, run_id, authority, actor, digest):
    decision, evidence, identity = _artifact(store, run_id, authority)
    verification, bundle = _review(store, run_id, authority, decision, identity, evidence)
    authority.require_separation(verification["actor"], actor)
    if _digest(bundle) != digest:
        raise ValueError("Review digest does not match the reviewed artifacts")
    payload = {**identity, "review_sha256": digest, "status": "APPROVED"}
    previous = store.receipt(run_id, "review_approval", optional=True)
    if previous:
        prior = previous["payload"]
        if (previous["actor"] == actor and all(prior.get(key) == value for key, value in payload.items())
                and authority.validate_seal("approvers", "review", actor, prior, previous["mac"])):
            return prior
        raise ValueError("A different final review approval is already recorded")
    payload["approved_at"] = datetime.now(UTC).isoformat()
    signature = authority.seal("approvers", "review", actor, payload)
    store.record_receipt(run_id, "review_approval", {"actor": actor, "payload": payload, "mac": signature})
    return payload


def validate(store, run_id, authority, *, persist=True):
    decision, evidence, identity = _artifact(store, run_id, authority)
    verification, bundle = _review(store, run_id, authority, decision, identity, evidence)
    approval = store.receipt(run_id, "review_approval")
    authority.require_separation(verification["actor"], approval["actor"])
    payload = approval["payload"]
    if (any(payload.get(key) != value for key, value in identity.items())
            or payload["review_sha256"] != _digest(bundle)
            or not authority.validate_seal("approvers", "review", approval["actor"], payload, approval["mac"])):
        raise ValueError("Final review approval is stale or unauthenticated")
    result = verification["payload"]
    actual = result["environment"] == "LOCAL_ACTUAL"
    report = {
        **identity, "review_sha256": _digest(bundle),
        "status": "LOCAL_DEV_VALIDATED" if actual else "OFFLINE_VALIDATED",
        "environment": result["environment"], "checks": result["checks"],
    }
    return store.record_receipt(run_id, "validation", report) if persist else report


def scoring_inputs(store, run_id, authority):
    """Return only authenticated facts; absence of an independent measure earns no points."""
    decision, evidence, identity = _artifact(store, run_id, authority)
    checks = {}
    observed = store.receipt(run_id, "actual_verification", optional=True)
    receipts = {**identity, "requirements_approval": {
        "actor": decision["actor"], "decided_at": decision["decided_at"],
    }}
    gates = {"requirements_approval": True}
    if observed:
        observed = _read_verification(
            store, run_id, authority, decision, identity, evidence, allow_blocked=True,
        )
        receipts["actual_verification"] = observed["payload"]
        checks = {row["check_id"]: row["result"] == "PASSED" for row in observed["payload"]["checks"]}
        gates.update(
            independent_tests=all(checks[name] for name in ("acceptance", "regression", "generated", "quality", "definition_of_done")),
            security=checks["security"],
        )
        if not checks["dev"]:
            gates["dev_validation"] = False
        if store.receipt(run_id, "review_approval", optional=True):
            validated = validate(store, run_id, authority, persist=False)
            receipts["final_validation"] = validated
            gates["review_approval"] = gates["dev_validation"] = True
    return receipts, checks, gates
