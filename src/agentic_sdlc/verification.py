"""Bind independent local attestations and final operator approval to exact artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .agents.review import ReviewAgent
from .development import (
    DevelopmentGuardrails,
    agent_model_config,
    model_identity,
    validate_provider,
    verify_artifact,
)
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


def _finding_records(findings):
    records = {}
    severity_order = {name: level for level, name in enumerate(("info", "low", "medium", "high", "critical"))}
    for finding in findings:
        row = finding.to_dict()
        origin = "ai" if finding.reviewer == "llm" else "tool"
        if row["severity"] not in severity_order:
            raise ValueError("Review returned an unsupported finding severity")
        identifier = _digest([origin, *finding.key, " ".join(finding.message.split()).casefold()])
        row.update(id=identifier, origin=origin, adjudication="unverified", resolution="unverified")
        previous = records.get(identifier)
        if previous is None or severity_order[row["severity"]] > severity_order[previous["severity"]]:
            records[identifier] = row
    return list(records.values())


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
    safe_config = config.with_overrides({"review": {"external_tools": "none"}})
    real_model = evidence["execution_mode"] != "OFFLINE_STUB"
    if real_model:
        inputs = evidence["inputs"]
        frozen_budget = inputs.get("budget_policy_sha256")
        if frozen_budget and frozen_budget != _digest(config.get("enterprise.budgets", {})):
            raise ValueError("Review requires the original budget policy")
        if any(config.get(f"llm.{setting}") != inputs[key]
               for setting, key in (("provider", "provider"), ("model", "model"), ("base_url", "endpoint"))):
            raise ValueError("Review requires the original development provider configuration")
        safe_config = agent_model_config(safe_config, "review")
        expected = evidence.get("model_profiles", {}).get("review")
        if (expected and model_identity(safe_config) != expected) or safe_config.get("llm.provider") == "stub":
            raise ValueError("Review requires the approved model profile recorded during development")
    else:
        safe_config = safe_config.with_overrides({"llm": {"provider": "stub"}})
    validate_provider(safe_config, allow_provider)
    llm = make_llm(safe_config)
    agent = ReviewAgent(llm, DevelopmentGuardrails(safe_config), safe_config)
    root = Path(evidence["artifact_dir"]) / "integrated"
    findings = agent.security(root, work)
    pattern, trace = agent.pattern(root, work)
    findings.extend(pattern)
    findings.extend(agent.llm_vote(root, work))
    records = _finding_records(findings)
    historical = [row["record"] for row in store.historical_receipts(run_id) if row["kind"] == "review"]
    previous_ids = {finding["id"] for finding in historical[-1]["findings"] if finding.get("id")} if historical else set()
    current_ids = {finding["id"] for finding in records}
    payload = {
        **identity, "verification_sha256": _digest(verification),
        "status": "BLOCKED" if any(f.severity in {"critical", "high"} for f in findings)
        else "WAITING_REVIEW_APPROVAL",
        "trace": trace, "findings": records,
        "finding_comparison": {"repeated": sorted(previous_ids & current_ids),
                               "not_observed_in_attempt": sorted(previous_ids - current_ids)},
        "provider_usage": llm.usage() if real_model else [],
    }
    store.record_receipt(run_id, "review", payload)
    return {**payload, "review_sha256": _digest(payload)}


def reject_review(store, run_id, authority, actor, digest, reason):
    decision, evidence, identity = _artifact(store, run_id, authority)
    verification, bundle = _review(store, run_id, authority, decision, identity, evidence, allow_blocked=True)
    authority.require_separation(verification["actor"], actor)
    if _digest(bundle) != digest or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError("Review rejection requires its exact digest and a bounded reason")
    payload = {**identity, "review_sha256": digest, "status": "REJECTED", "reason": reason}
    previous = store.receipt(run_id, "review_rejection", optional=True)
    if previous:
        prior = previous["payload"]
        if (previous["actor"] == actor and all(prior.get(key) == value for key, value in payload.items())
                and authority.validate_seal("approvers", "review_rejection", actor, prior, previous["mac"])):
            return prior
        raise ValueError("A different final review rejection is already recorded")
    payload["rejected_at"] = datetime.now(UTC).isoformat()
    mac = authority.seal("approvers", "review_rejection", actor, payload)
    store.record_receipt(run_id, "review_rejection", {"actor": actor, "payload": payload, "mac": mac})
    return payload


def adjudicate_review(store, run_id, authority, actor, labels):
    from .metrics import independently_reviewed

    decision, evidence, identity = _artifact(store, run_id, authority)
    _, review = _review(store, run_id, authority, decision, identity, evidence, allow_blocked=True)
    if labels.get("review_sha256") != _digest(review):
        raise ValueError("Adjudication must identify the exact review digest")
    findings = {(item["origin"], item["id"]) for item in review["findings"] if "id" in item}
    if any((item.get("origin"), item.get("id")) not in findings for item in labels["findings"]):
        raise ValueError("Adjudication contains findings absent from the selected review")
    metrics = independently_reviewed(labels["defects"], labels["findings"])
    payload = {**identity, "review_sha256": labels["review_sha256"], "defects": labels["defects"],
               "findings": labels["findings"], "metrics": metrics}
    mac = authority.seal("approvers", "review_adjudication", actor, payload)
    store.record_receipt(run_id, "review_adjudication", {"payload": payload, "actor": actor, "mac": mac})
    return payload


def _review(store, run_id, authority, decision, identity, evidence, *, allow_blocked=False):
    verification = _read_verification(store, run_id, authority, decision, identity, evidence)
    bundle = store.receipt(run_id, "review")
    if (any(bundle.get(key) != value for key, value in identity.items())
            or bundle["verification_sha256"] != _digest(verification)):
        raise ValueError("Review refers to stale verification evidence")
    if bundle["status"] != "WAITING_REVIEW_APPROVAL" and not allow_blocked:
        raise ValueError("Review found a blocking issue")
    return verification, bundle


def remediation_context(store, run_id, authority):
    decision, evidence, identity = _artifact(store, run_id, authority)
    receipt = _read_verification(store, run_id, authority, decision, identity, evidence, allow_blocked=True)
    payload = receipt["payload"]
    if payload["environment"] != "LOCAL_ACTUAL":
        raise ValueError("Correction requires authenticated actual verification")
    if payload["status"] == "BLOCKED":
        failed = [check for check in payload["checks"] if check["result"] != "PASSED"]
        if not failed or any(check["result"] != "FAILED" for check in failed):
            raise ValueError("Infrastructure failures require operator resolution before correction")
        feedback = [{"check": check["check_id"], "exit_code": check["exit_code"]} for check in failed]
    else:
        _, bundle = _review(store, run_id, authority, decision, identity, evidence, allow_blocked=True)
        rejection = store.receipt(run_id, "review_rejection", optional=True)
        if bundle["status"] == "BLOCKED":
            feedback = bundle["findings"]
        elif rejection:
            rejected = rejection["payload"]
            if (any(rejected.get(key) != value for key, value in identity.items())
                    or rejected.get("review_sha256") != _digest(bundle)
                    or not authority.validate_seal("approvers", "review_rejection", rejection["actor"],
                                                   rejected, rejection["mac"])):
                raise ValueError("Final review rejection is stale or unauthenticated")
            feedback = [{"operator_reason": rejected["reason"]}, *bundle["findings"]]
        else:
            raise ValueError("Correction requires a failed check, blocking review or explicit human rejection")
    return {"source_attempt": evidence["attempt"], "source": evidence["artifact_dir"],
            "manifest_sha256": evidence["manifest_sha256"], "feedback": feedback[:20]}


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


def attest_delivery(store, run_id, authority, config, actor, kind, document, raw_directory=None):
    from .delivery_evidence import collect_measurements, validate_ci, verify_evaluator_archive

    decision, artifact, binding = _artifact(store, run_id, authority)
    authority.require_separation(actor, decision["actor"])
    if kind == "ci_evidence":
        import tempfile

        from .github_evidence import collect, download_artifact

        policy = config.get("enterprise.delivery_policy.remote_ci", {})
        if not policy.get("required"):
            raise ValueError("Configure trusted required CI before recording an attestation")
        evidence = collect(config, policy["repository"], document["run_id"], document["attempt"],
                           document["commit"], policy["workflow"])
        validate_ci(policy, evidence, {key: value for key, value in binding.items() if key != "run_id"}, document)
        signed_archive = policy.get("signed_archive")
        if signed_archive not in policy["artifacts"]:
            raise ValueError("Select the mandatory CI archive containing the evaluator signature")
        authenticated = None
        with tempfile.TemporaryDirectory(prefix="capstone-attested-ci-") as directory:
            by_name = {row["name"]: row for row in evidence["run_artifact_metadata"]}
            for name in policy["artifacts"]:
                artifact = by_name[name]
                archive_path = Path(directory) / f"{artifact['id']}.zip"
                download_artifact(config, evidence, artifact["id"], archive_path)
                if name == signed_archive:
                    authenticated = verify_evaluator_archive(config, policy, archive_path, document)
        value = {"github": evidence, "statement": document, "evaluator": authenticated}
    else:
        if document.get("manifest_sha256") != binding["manifest_sha256"]:
            raise ValueError("Criterion evidence does not match the current artifact")
        if raw_directory is None:
            raise ValueError("Supply the trusted raw-evidence directory")
        criteria = collect_measurements(config, document, raw_directory, binding, authority, actor, artifact["attempt"])
        if artifact["execution_mode"] != "OFFLINE_STUB" and any(row["source"] == "SYNTHETIC" for row in criteria.values()):
            raise ValueError("Synthetic criterion evidence cannot score actual execution")
        value = {"criteria": criteria, "rubric_version": document["rubric_version"]}
    payload = {**binding, **value}
    mac = authority.seal("verifiers", kind, actor, payload)
    store.record_receipt(run_id, kind, {"actor": actor, "payload": payload, "mac": mac})
    return payload


def _delivery_attestation(store, run_id, authority, decision, identity, kind):
    receipt = store.receipt(run_id, kind, optional=True)
    if receipt is None:
        return None
    authority.require_separation(receipt["actor"], decision["actor"])
    payload = receipt["payload"]
    if (any(payload.get(key) != value for key, value in identity.items())
            or not authority.validate_seal("verifiers", kind, receipt["actor"], payload, receipt["mac"])):
        raise ValueError("Independent delivery evidence is stale or unauthenticated")
    return payload


def validate(store, run_id, authority, *, persist=True, config=None, authorize_remote=False):
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
    report = {
        **identity, "review_sha256": _digest(bundle), "status": "OFFLINE_VALIDATED",
        "environment": result["environment"], "checks": result["checks"],
    }
    policy = config.get("enterprise.delivery_policy.remote_ci", {}) if config else {}
    frozen = evidence.get("inputs", {}).get("delivery_policy_sha256")
    if frozen and (not config or frozen != _digest([policy, config.get("enterprise.remote_dev", {})])):
        raise ValueError("Validation requires the original delivery policy")
    if policy.get("required"):
        from .delivery_evidence import validate_ci

        ci = _delivery_attestation(store, run_id, authority, decision, identity, "ci_evidence")
        if ci is None and not persist:
            return {**report, "status": "NOT_EVALUATED"}
        if ci is None or not ci.get("evaluator") or result["environment"] != "LOCAL_ACTUAL":
            raise ValueError("Actual independently attested CI evidence is required for delivery")
        validate_ci(policy, ci["github"], {key: value for key, value in identity.items() if key != "run_id"}, ci["statement"])
        report["remote_ci"] = "GITHUB_ACTIONS_ACTUAL"
    if result["environment"] == "LOCAL_ACTUAL":
        report = _post_approval_dev(store, run_id, authority, config, evidence, verification,
                                    approval, report, persist=persist)
    remote_policy = config.get("enterprise.remote_dev", {}) if config else {}
    if remote_policy.get("required") and result["environment"] != "LOCAL_ACTUAL":
        raise ValueError("Remote DEV requires actual independent verification")
    if remote_policy.get("required") and report["status"] == "LOCAL_DEV_VALIDATED":
        report = _remote_validation(store, run_id, authority, decision, identity, approval, config, report,
                                    persist=persist, authorized=authorize_remote)
    if not persist:
        return report
    existing = store.receipt(run_id, "validation", optional=True)
    if existing and "post_approval_dev" in report and "post_approval_dev" not in existing:
        return store.record_receipt(run_id, "validation_v2", report)
    return store.record_receipt(run_id, "validation", report)


def _remote_validation(store, run_id, authority, decision, identity, approval, config, report, *, persist, authorized):
    from .remote_dev import run

    kind = "remote_dev_validation"
    receipt = _delivery_attestation(store, run_id, authority, decision, identity, kind)
    if receipt is None and not persist:
        return {**report, "status": "NOT_EVALUATED"}
    if receipt is None:
        if not authorized:
            raise ValueError("Remote DEV execution requires explicit --authorize-remote-dev")
        intent = store.receipt(run_id, "remote_dev_intent", optional=True)
        binding = {**identity, "approval_sha256": _digest(approval),
                   "environment_policy_sha256": _digest(config.get("enterprise.remote_dev"))}
        if intent and intent != binding:
            raise ValueError("Remote DEV recovery requires the original authenticated policy and approval")
        if not intent:
            store.record_receipt(run_id, "remote_dev_intent", binding)
        result = run(config, binding, create=intent is None)
        verifier = store.receipt(run_id, "actual_verification")["actor"]
        payload = {**identity, "result": result, "binding": binding}
        mac = authority.seal("verifiers", kind, verifier, payload)
        store.record_receipt(run_id, kind, {"actor": verifier, "payload": payload, "mac": mac})
        receipt = payload
    binding = receipt["binding"]
    if (binding["approval_sha256"] != _digest(approval)
            or binding["environment_policy_sha256"] != _digest(config.get("enterprise.remote_dev"))):
        raise ValueError("Remote DEV receipt does not match current approval and policy")
    result = receipt["result"]
    return {**report, "status": "REMOTE_DEV_VALIDATED" if result["status"] == "PASSED" else "BLOCKED",
            "remote_dev": result}


def _post_approval_dev(store, run_id, authority, config, evidence, verification, approval, report, *, persist):
    actor = verification["actor"]
    binding = {key: report[key] for key in ("run_id", "spec_sha256", "manifest_sha256", "review_sha256")}
    binding["review_approval_sha256"] = _digest(approval)
    receipt = store.receipt(run_id, "dev_validation", optional=True)
    if receipt is None and not persist:
        return {**report, "status": "NOT_EVALUATED"}
    if receipt is None:
        if config is None:
            raise ValueError("Post-approval DEV validation requires trusted Docker configuration")
        from .container_verifier import DockerVerifier

        root = Path(evidence["artifact_dir"])
        expected = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["integrated"]
        runner = DockerVerifier(config)
        preflight = next(check for check in verification["payload"]["checks"] if check["check_id"] == "dev")
        command_sha256 = hashlib.sha256(json.dumps(runner.checks["dev"]).encode()).hexdigest()
        if (runner.image, command_sha256) != (preflight["image_id"], preflight["command_sha256"]):
            raise ValueError("Post-approval DEV must use the verified image and command")
        check = runner.run_dev(root / "integrated", binding, expected)
        verify_artifact(evidence)
        receipt = {"payload": check, "mac": authority.seal("verifiers", "dev_validation", actor, check)}
        receipt = store.record_receipt(run_id, "dev_validation", receipt)
    check = receipt["payload"]
    if (any(check.get(key) != value for key, value in binding.items())
            or not authority.validate_seal("verifiers", "dev_validation", actor, check, receipt["mac"])):
        raise ValueError("Post-approval DEV evidence is stale or unauthenticated")
    success = check["result"] == "PASSED" and check["exit_code"] == 0
    return {**report, "status": "LOCAL_DEV_VALIDATED" if success else "BLOCKED", "post_approval_dev": check}


def scoring_inputs(store, run_id, authority, *, include_final=True, config=None):
    """Return only authenticated facts; absence of an independent measure earns no points."""
    decision, evidence, identity = _artifact(store, run_id, authority)
    checks = {}
    observed = store.receipt(run_id, "actual_verification", optional=True)
    receipts = {**identity, "attempt_id": evidence["attempt"], "requirements_approval": {
        "actor": decision["actor"], "decided_at": decision["decided_at"],
    }}
    gates = {"requirements_approval": True}
    criteria = _delivery_attestation(store, run_id, authority, decision, identity, "criterion_evidence")
    if observed and criteria and config and criteria["rubric_version"] == config.get("evaluation.rubric.version"):
        receipts["criterion_evidence"] = criteria
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
        if include_final and store.receipt(run_id, "review_approval", optional=True):
            validated = validate(store, run_id, authority, persist=False, config=config)
            receipts["final_validation"] = validated
            gates["review_approval"] = True
            if validated.get("remote_ci") == "GITHUB_ACTIONS_ACTUAL":
                gates["remote_ci"] = True
            if validated["status"] != "NOT_EVALUATED":
                gates["dev_validation"] = validated["status"] in {"LOCAL_DEV_VALIDATED", "REMOTE_DEV_VALIDATED"}
    return receipts, checks, gates
