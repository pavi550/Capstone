"""Bind independent local attestations and final operator approval to exact artifacts."""

from __future__ import annotations

import hashlib
import json
import re
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
    if config.get("enterprise.delivery_policy.remote_ci.dispatch_ref"):
        initial_ci = _delivery_attestation(store, run_id, authority, decision, identity, "ci_evidence")
        if initial_ci is None:
            raise ValueError("Complete initial trusted CI before review")
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


def local_review_evaluation(store, run_id, authority, config, actor):
    """Evaluate the stored review with operator-controlled, artifact-specific labels."""
    from .delivery_evidence import evaluate_review

    if config.get("enterprise.delivery_policy.remote_ci.required", False):
        raise ValueError("Local review evidence cannot satisfy required remote CI")
    decision, evidence, identity = _artifact(store, run_id, authority)
    _, review = _review(store, run_id, authority, decision, identity, evidence, allow_blocked=True)
    saved = _local_review_receipt(store, run_id, authority, decision, identity, review)
    if saved is not None:
        return saved
    folder = config.get("enterprise.delivery_policy.local_review.reference_dir")
    if not folder:
        return {"status": "NOT_EVALUATED", "reason": "No independent local review reference directory"}
    root = Path(folder).resolve()
    artifact = Path(evidence["artifact_dir"]).resolve()
    frozen_source = evidence.get("inputs", {}).get("source")
    if (root.is_relative_to(artifact.parent)
            or (frozen_source and root.is_relative_to(Path(frozen_source).resolve()))):
        raise ValueError("Keep local review references outside generated and evaluated workspaces")
    path = root / (identity["manifest_sha256"] + ".json")
    if not path.is_file():
        return {"status": "NOT_EVALUATED", "reason": "Independent labels are unavailable for this artifact"}
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError("Invalid local review reference")
    authority.require_separation(actor, decision["actor"])
    raw = path.read_bytes()
    evaluation = evaluate_review(review, json.loads(raw), identity)
    payload = {**identity, "attempt_id": evidence["attempt"], "review_sha256": _digest(review),
               "reference_sha256": hashlib.sha256(raw).hexdigest(), "metrics": evaluation["metrics"],
               "source": "LOCAL_INDEPENDENT_REVIEW", "status": "EVALUATED"}
    store.record_receipt(run_id, "local_review_evidence", {
        "actor": actor, "payload": payload,
        "mac": authority.seal("verifiers", "local_review_evidence", actor, payload),
    })
    return payload


def _local_review_receipt(store, run_id, authority, decision, identity, review):
    receipt = _delivery_attestation(store, run_id, authority, decision, identity, "local_review_evidence")
    if receipt is not None and receipt.get("review_sha256") != _digest(review):
        raise ValueError("Local independent evaluation belongs to a stale review")
    return receipt


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


def export_ci_bundle(store, run_id, authority, output, *, include_review=False):
    """Prepare an immutable directory from the active approved artifact."""
    from .development import _snapshot

    decision, evidence, identity = _artifact(store, run_id, authority)
    source = Path(evidence["artifact_dir"]).resolve()
    output = output.resolve()
    if output.is_relative_to(source):
        raise ValueError("CI output must be outside the development artifact")
    review = _review(store, run_id, authority, decision, identity, evidence, allow_blocked=True)[1] if include_review else None
    output.mkdir()
    _snapshot(source / "integrated", output / "integrated")
    (output / "manifest.json").write_bytes((source / "manifest.json").read_bytes())
    verify_artifact({"artifact_dir": str(output), "manifest_sha256": identity["manifest_sha256"]})
    members = {"specification.json": decision["draft"]}
    if review is not None:
        members["review.json"] = review
    for name, payload in members.items():
        (output / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**identity, "attempt": evidence["attempt"], "bundle": str(output),
            "review_sha256": _digest(review) if review is not None else None}


def submit_ci(store, run_id, authority, config, actor, *, include_review, authorized):
    import tempfile
    from contextlib import closing
    from zipfile import ZIP_DEFLATED, ZipFile

    from .development import _manifest
    from .github_evidence import dispatch, monitor, publish_bundle

    policy = config.get("enterprise.delivery_policy.remote_ci", {})
    repository, workflow = policy["repository"], policy["workflow"]
    ref, commit = policy["dispatch_ref"], policy["dispatch_commit"]
    branch = config.get("github.bundle_branch", "")
    if (not policy.get("required") or not re.fullmatch(r"[a-f0-9]{40}", commit)
            or branch == ref or ref not in config.get("github.dispatch_refs", [])):
        raise ValueError("Configure a distinct bundle branch and a pinned protected evaluator revision")
    decision, evidence, binding = _artifact(store, run_id, authority)
    authority.require_separation(actor, decision["actor"])
    review = _review(store, run_id, authority, decision, binding, evidence, allow_blocked=True)[1] if include_review else None
    if include_review and not store.receipt(run_id, "ci_evidence", optional=True):
        raise ValueError("Complete initial CI evaluation before submitting review evidence")
    record = {**binding, "repository": repository, "workflow": workflow, "commit": commit,
              "development_attempt": evidence["attempt"], "review_sha256": _digest(review) if review is not None else None}
    correlation = _digest(record)

    def save(state):
        with closing(store._connect()) as conn, conn:
            conn.execute("INSERT INTO ci_submissions VALUES (?, ?, ?, ?) ON CONFLICT(run_id, correlation) "
                         "DO UPDATE SET state=excluded.state, record=excluded.record",
                         (run_id, correlation, state, json.dumps(record)))

    with closing(store._connect()) as conn:
        existing = conn.execute("SELECT state, record FROM ci_submissions WHERE run_id=? AND correlation=?",
                                (run_id, correlation)).fetchone()
    if existing:
        if existing["state"] not in {"DISPATCHED", "COMPLETED"}:
            raise ValueError("Uncertain CI publication or dispatch; reconcile before attempting replay")
        record = json.loads(existing["record"])
        if existing["state"] == "COMPLETED":
            kind = record["receipt_kind"]
            return _delivery_attestation(store, run_id, authority, decision, binding, kind)
    else:
        if not authorized or not config.get("github.allow_dispatch") or not config.get("github.allow_bundle_publication"):
            raise ValueError("CI publication and dispatch require explicit authorization and enabled policy")
        with tempfile.TemporaryDirectory() as tmp:
            bundle, path = Path(tmp) / "bundle", Path(tmp) / "input.zip"
            export_ci_bundle(store, run_id, authority, bundle, include_review=include_review)
            with ZipFile(path, "x", compression=ZIP_DEFLATED) as archive:
                for member in _manifest(bundle):
                    archive.write(bundle / member, member)
            if path.stat().st_size > 10_000_000:
                raise ValueError("CI bundle exceeds the publication limit")
            data = path.read_bytes()
        record.update(bundle_sha256=hashlib.sha256(data).hexdigest(), correlation=correlation)
        save("PUBLISHING")
        name = f"capstone-bundles/{correlation}.zip"
        record["publication_commit"] = publish_bundle(config, repository, branch, name, data)
        save("DISPATCHING")
        selection = dispatch(config, repository, workflow, ref, {
            "bundle": name, "bundle_ref": record["publication_commit"], "bundle_sha256": record["bundle_sha256"],
            "manifest_sha256": binding["manifest_sha256"], "spec_sha256": binding["spec_sha256"],
        }, exact=True)
        record.update(ci_run_id=selection["run_id"], ci_attempt=selection["attempt"])
        save("DISPATCHED")
    fetched = monitor(store, run_id, config, {"run_id": record["ci_run_id"], "attempt": record["ci_attempt"], "commit": commit})
    document = {**record, "run_id": fetched["run_id"], "attempt": fetched["attempt"], "workflow": fetched["workflow"],
                "evaluator_revision": policy["evaluator_revision"],
                "verified_archives": {row["name"]: row["digest"] for row in fetched["run_artifact_metadata"]}}
    result = attest_delivery(store, run_id, authority, config, actor, "ci_evidence", document)
    record["receipt_kind"] = "ci_review_evidence" if include_review else "ci_evidence"
    save("COMPLETED")
    return result


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
        if document.get("bundle_sha256") and authenticated.get("bundle_sha256") != document["bundle_sha256"]:
            raise ValueError("Evaluator signed a different published bundle")
        review_evaluation = authenticated.get("review_evaluation")
        if document.get("review_sha256") and (not review_evaluation
                or review_evaluation.get("findings_sha256") != document["review_sha256"]):
            raise ValueError("Required independent review evaluation is missing or stale")
        if review_evaluation:
            if store.receipt(run_id, "ci_evidence", optional=True):
                kind = "ci_review_evidence"
            candidate = store.receipt(run_id, "review", optional=True)
            if candidate is None or _digest(candidate) != review_evaluation["findings_sha256"]:
                raise ValueError("Trusted CI review evaluation does not match the stored review findings")
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
        if policy.get("dispatch_ref"):
            review_ci = _delivery_attestation(store, run_id, authority, decision, identity, "ci_review_evidence")
            if review_ci is None:
                if not persist:
                    return {**report, "status": "NOT_EVALUATED"}
                raise ValueError("Complete independent CI review evaluation before delivery")
            independent = review_ci["evaluator"].get("review_evaluation")
            if not independent or independent.get("findings_sha256") != _digest(bundle):
                raise ValueError("Independent CI review does not match the approved review")
            validate_ci(policy, review_ci["github"], {key: value for key, value in identity.items() if key != "run_id"},
                        review_ci["statement"])
        report["remote_ci"] = "GITHUB_ACTIONS_ACTUAL"
    local_policy = config.get("enterprise.delivery_policy.local_review", {}) if config else {}
    frozen_local = evidence.get("inputs", {}).get("local_review_policy_sha256")
    if frozen_local and frozen_local != _digest(local_policy):
        raise ValueError("Validation requires the original local review policy")
    if not policy.get("required") and local_policy.get("required"):
        local = _local_review_receipt(store, run_id, authority, decision, identity, bundle)
        if local is None:
            if not persist:
                return {**report, "status": "NOT_EVALUATED"}
            raise ValueError("Independent local review evidence is required before delivery")
        local_record = store.receipt(run_id, "local_review_evidence")
        authority.require_separation(local_record["actor"], approval["actor"])
        report["local_review"] = local["source"]
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
        intent = store.receipt(run_id, "remote_dev_intent", optional=True)
        if not intent and not authorized:
            raise ValueError("Remote DEV execution requires explicit --authorize-remote-dev")
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
    if observed and config and not config.get("enterprise.delivery_policy.remote_ci.required", False):
        review = store.receipt(run_id, "review", optional=True)
        if review is not None:
            local = _local_review_receipt(store, run_id, authority, decision, identity, review)
            if local is not None:
                receipts["local_review_evidence"] = local
    if config and config.get("enterprise.delivery_policy.remote_ci.required", False):
        ci = _delivery_attestation(store, run_id, authority, decision, identity, "ci_evidence")
        if ci:
            receipts["ci_evidence"] = ci
        ci_review = _delivery_attestation(store, run_id, authority, decision, identity, "ci_review_evidence")
        if ci_review:
            receipts["ci_review_evidence"] = ci_review
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
        ci_checks = receipts.get("ci_evidence", {}).get("evaluator", {}).get("checks", {})
        if ci_checks.get("traceability") in {"PASSED", "FAILED"}:
            checks["ci_traceability"] = ci_checks["traceability"] == "PASSED"
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
