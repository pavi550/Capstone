"""Bind independent evaluator statements to a single verified workspace revision."""

import hashlib
import hmac
import json
import re
import zipfile
from datetime import UTC, datetime

CRITERIA = {
    "extraction": "ground_truth", "criteria_completeness": "human_adjudicated",
    "ambiguity": "ground_truth", "traceability": "deterministic",
    "correctness": "independent_measurement", "completeness": "human_adjudicated",
    "maintainability": "human_adjudicated", "scope": "baseline_dependent",
    "acceptance_coverage": "independent_measurement", "positive": "ground_truth",
    "negative": "ground_truth", "edges": "ground_truth", "functional_tests": "independent_measurement",
    "regression": "deterministic", "coverage": "independent_measurement", "build_lint": "deterministic",
    "defect_detection": "ground_truth", "finding_accuracy": "human_adjudicated",
    "compliance": "human_adjudicated", "actionability": "human_adjudicated", "path_policy": "deterministic",
    "secrets": "deterministic", "security": "independent_measurement", "permissions": "independent_measurement",
    "review_effort": "baseline_dependent", "corrections": "baseline_dependent", "human_changes": "baseline_dependent",
    "final_acceptance": "independent_measurement", "definition_of_done": "deterministic", "artifacts": "deterministic",
}


def registry(config):
    rubric = config.get("evaluation.rubric")
    return {key: {"criterion_id": key, "criterion_name": key.replace("_", " "), "stage": stage,
                  "evidence_source": "authenticated original observations and digest-verified attachments",
                  "collection_method": "collect_measurements", "verification_method": "identity, attempt, signature and source-byte digests",
                  "implementation_status": "PARTIALLY_IMPLEMENTED", "maximum_points": points,
                  "required_evidence_types": [CRITERIA.get(key, "human_adjudicated")],
                  "evaluation_method": CRITERIA.get(key, "human_adjudicated"),
                  "scoring_function": "earned = maximum * passed / measured", "mandatory_gate": None,
                  "applicability": "requires independently recorded observations", "evaluator_version": rubric["version"]}
            for stage, criteria in rubric["criteria"].items() for key, points in criteria.items()}


def measurements(config, document):
    criteria = registry(config)
    if document.get("rubric_version") != config.get("evaluation.rubric.version") or not document.get("criteria"):
        raise ValueError("Criterion evidence requires the active rubric version and at least one measurement")
    for name, row in document["criteria"].items():
        if name not in criteria or row.get("method") != criteria[name]["evaluation_method"]:
            raise ValueError("Unknown criterion or incompatible evidence method")
        passed, measured = row.get("passed"), row.get("measured")
        if type(passed) is not int or type(measured) is not int or not 0 <= passed <= measured or measured == 0:
            raise ValueError("Criterion counts require 0 <= passed <= measured and a positive denominator")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip() or not row.get("sources"):
            raise ValueError("Criterion evidence needs independently traceable source digests and a reason")
        if any(not re.fullmatch(r"[a-f0-9]{64}", str(digest)) for digest in row["sources"]):
            raise ValueError("Criterion evidence sources must be SHA-256 digests")
    return document["criteria"]



def collect_measurements(config, document, directory, binding, authority, actor, attempt):
    """Verify original signed observations and their raw attachments before deriving counts."""
    from pathlib import Path

    definitions = registry(config)
    root = Path(directory).resolve()
    entries = document.get("raw_evidence")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("Criterion attestation requires original raw evidence files")
    derived = {}

    def read(reference):
        if not isinstance(reference, dict):
            raise TypeError("Invalid raw evidence reference")
        relative = Path(reference["path"])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or path.is_symlink() or path.stat().st_size > 1_000_000:
            raise ValueError("Raw evidence path escapes its bounded trusted directory")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference.get("sha256"):
            raise ValueError("Raw evidence digest mismatch")
        return data

    for reference in entries:
        record = json.loads(read(reference))
        payload = record["payload"]
        name = payload["criterion_id"]
        if name not in definitions or name in derived:
            raise ValueError("Unknown or duplicated criterion source")
        if (record.get("actor") != actor or not authority.validate_seal("verifiers", "criterion-source", actor,
                                                                      payload, record["mac"])):
            raise ValueError("Raw evidence evaluator identity is not authenticated")
        if any(payload.get(key) != value for key, value in binding.items()) or payload.get("attempt_id") != attempt:
            raise ValueError("Raw criterion evidence is stale or belongs to another artifact or attempt")
        if payload.get("rubric_version") != config.get("evaluation.rubric.version"):
            raise ValueError("Raw criterion evidence uses another rubric")
        method = definitions[name]["evaluation_method"]
        if payload.get("method") != method or payload.get("source") not in {"LOCAL_ACTUAL", "GITHUB_ACTIONS_ACTUAL", "SYNTHETIC"}:
            raise ValueError("Raw criterion evidence has an incompatible method or source")
        attachments = payload.get("attachments", [])
        if not attachments:
            raise ValueError("Original evidence attachments are required, not numerical scores alone")
        for attachment in attachments:
            read(attachment)
        observations = payload.get("observations")
        if not isinstance(observations, list) or not 1 <= len(observations) <= 10000:
            raise ValueError("Raw evidence requires bounded observations")
        ids, passed = set(), 0
        for observation in observations:
            identifier = observation.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in ids:
                raise ValueError("Raw observation IDs must be distinct")
            ids.add(identifier)
            if method == "human_adjudicated":
                if observation.get("verdict") not in {"PASS", "FAIL"} or not observation.get("rationale"):
                    raise ValueError("Human adjudication requires a verdict and rationale for each observation")
                accepted = observation["verdict"] == "PASS"
            elif method == "baseline_dependent":
                base, actual = observation.get("baseline"), observation.get("actual")
                if (type(base) not in {int, float} or type(actual) not in {int, float}
                        or not 0 <= actual < float("inf") or not 0 < base < float("inf")
                        or not observation.get("equivalent_task_identity")):
                    raise ValueError("Baseline evidence requires finite equivalent measured task observations")
                accepted = actual <= base
            else:
                if "expected" not in observation or "observed" not in observation:
                    raise ValueError("Raw observations require expected and observed values")
                accepted = observation["expected"] == observation["observed"]
            passed += int(accepted)
        derived[name] = {"method": method, "passed": passed, "measured": len(observations),
                         "reason": "Derived from authenticated raw observations and verified source bytes",
                         "sources": [reference["sha256"], *[row["sha256"] for row in attachments]],
                         "source": payload["source"]}
    return measurements(config, {"rubric_version": config.get("evaluation.rubric.version"), "criteria": derived})


def validate_ci(policy, evidence, binding, statement, *, now=None):
    if not isinstance(policy, dict) or not policy.get("required"):
        raise ValueError("Required remote CI must be configured in trusted delivery policy")
    expected = {"repository": policy["repository"], "workflow": f".github/workflows/{policy['workflow']}"}
    if any(evidence.get(key) != value for key, value in expected.items()) or evidence.get("conclusion") != "success":
        raise ValueError("CI workflow identity or conclusion does not satisfy delivery policy")
    if any(statement.get(key) != value for key, value in binding.items()):
        raise ValueError("CI attestation does not identify the current artifact and specification")
    if any(statement.get(key) != evidence.get(key) for key in ("commit", "run_id", "attempt", "repository", "workflow")):
        raise ValueError("CI attestation does not match fetched run identity")
    revision = policy.get("evaluator_revision")
    if not re.fullmatch(r"[a-f0-9]{40}", str(revision)) or statement.get("evaluator_revision") != revision:
        raise ValueError("CI evaluator revision is not authorized")
    mandatory = policy.get("jobs", [])
    artifacts = policy.get("artifacts", [])
    jobs = evidence["jobs"]
    current = now or datetime.now(UTC)
    if not mandatory or not artifacts:
        raise ValueError("Remote CI requires named jobs and artifact archives")
    maximum_age = policy.get("max_age_seconds", 86400)
    if type(maximum_age) is not int or maximum_age <= 0:
        raise ValueError("CI freshness window must be a positive number of seconds")
    for name in mandatory:
        candidates = [job for job in jobs if job["name"] == name]
        if len(candidates) != 1 or candidates[0]["conclusion"] != "success" or candidates[0]["status"] != "completed":
            raise ValueError("Missing or unsuccessful required CI job")
        ended = datetime.fromisoformat(candidates[0]["completed_at"])
        if not 0 <= (current - ended).total_seconds() <= maximum_age:
            raise ValueError("Required CI job evidence is stale")
    for name in artifacts:
        rows = [row for row in evidence["run_artifact_metadata"] if row["name"] == name and not row["expired"]]
        digest = statement.get("verified_archives", {}).get(name)
        if len(rows) != 1 or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(digest)) or rows[0]["digest"] != digest:
            raise ValueError("Required CI archive is absent, expired or mismatched")


def verify_evaluator_archive(config, policy, archive_path, statement):
    key = config.secret("enterprise.delivery_policy.remote_ci.evaluator_key_env")
    if not key or len(key.encode()) < 32:
        raise ValueError("Configure a separate trusted CI evaluator signing key")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            matches = [item for item in archive.infolist() if item.filename == "attestation.json"]
            if len(matches) != 1 or matches[0].file_size > 100_000:
                raise ValueError("CI archive needs exactly one bounded attestation.json entry")
            with archive.open(matches[0]) as stream:
                record = json.loads(stream.read(100_001))
            raw = None
            raw_digest = record["payload"].get("raw_evidence_sha256")
            if raw_digest or policy.get("required"):
                entries = [item for item in archive.infolist() if item.filename == "raw-evidence.json"]
                if len(entries) != 1 or entries[0].file_size > 1_000_000:
                    raise ValueError("CI archive needs exactly one bounded raw-evidence.json entry")
                with archive.open(entries[0]) as stream:
                    raw = json.loads(stream.read(1_000_001))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("CI evidence archive is invalid") from exc
    payload = record["payload"]
    signature = record["mac"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    expected = hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise ValueError("CI evaluator signature is invalid")
    identity = ("run_id", "attempt", "commit", "repository", "workflow", "manifest_sha256", "spec_sha256",
                "evaluator_revision")
    if any(payload.get(name) != statement.get(name) for name in identity):
        raise ValueError("Trusted evaluator statement does not match the current artifact and CI attempt")
    if raw is not None:
        raw_signed = sign_evaluation(raw, {name: payload[name] for name in identity}, key)
        if raw_signed["payload"] != payload:
            raise ValueError("CI raw evidence differs from the signed evaluation")
    required = policy.get("checks", [])
    checks = payload.get("checks", {})
    if not required or any(checks.get(name) != "PASSED" for name in required):
        raise ValueError("Trusted evaluator did not report all required checks as passed")
    return payload


def unpack_bundle(source, destination, expected):
    """Extract a size-bounded bundle without links, traversal or duplicate entries."""
    from pathlib import PurePosixPath

    if not re.fullmatch(r"[a-f0-9]{64}", expected) or source.stat().st_size > 10_000_000:
        raise ValueError("Invalid published CI bundle")
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError("Published CI bundle digest mismatch")
    destination.mkdir()
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 10_000 or sum(entry.file_size for entry in entries) > 100_000_000:
            raise ValueError("Published CI bundle exceeds extraction limits")
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if (not name.parts or name.is_absolute() or ".." in name.parts or ":" in entry.filename
                    or chr(92) in entry.filename or entry.is_dir()
                    or (name.parts[0] != "integrated" and str(name) not in
                        {"manifest.json", "specification.json", "review.json"})):
                raise ValueError("Unexpected CI bundle archive member")
            target = destination.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(archive.read(entry))


def evaluate_review(candidate, reference, identity):
    from .metrics import independently_reviewed

    if any(candidate.get(key) != identity[key] or reference.get(key) != identity[key]
           for key in ("manifest_sha256", "spec_sha256")):
        raise ValueError("Review evaluation belongs to a different artifact")
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if reference.get("findings_sha256") != digest:
        raise ValueError("Independent reference belongs to different review findings")
    labels = reference["labels"]
    findings = [{"id": row["id"], "origin": "ai",
                 "adjudication": labels.get(row["id"], {}).get("adjudication", "unverified"),
                 "defect_id": labels.get(row["id"], {}).get("defect_id")}
                for row in candidate["findings"] if row.get("origin") == "ai"]
    if labels.keys() - {row["id"] for row in findings}:
        raise ValueError("Independent reference labels unknown findings")
    metrics = independently_reviewed(reference["defects"], findings)
    if not findings:
        metrics["ai"] = {"tp": 0, "fp": 0, "fn": len(reference["defects"]), "unverified": 0,
                         "derived": 0, "precision": None, "recall": 0.0 if reference["defects"] else None,
                         "f1": 0.0 if reference["defects"] else None}
    return {"findings_sha256": digest, "metrics": metrics}


def evaluate_bundle(config, bundle, acceptance, identity):
    """Trusted CI producer. Target code executes only inside DockerVerifier."""
    from .container_verifier import DockerVerifier
    from .development import DevelopmentGuardrails, verify_artifact
    from .specifications import specification_digest

    manifest_file = bundle / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    verify_artifact({"artifact_dir": str(bundle), "manifest_sha256": identity["manifest_sha256"]})
    specification = json.loads((bundle / "specification.json").read_text(encoding="utf-8"))
    if specification_digest(specification) != identity["spec_sha256"]:
        raise ValueError("CI specification does not match approved input")
    from pathlib import Path

    tests = acceptance / "tests"
    if not tests.is_dir() and (acceptance / "review-reference.json").exists():
        raise ValueError("Keep independent review labels outside the acceptance test directory")
    records = DockerVerifier(config).run(bundle / "integrated", tests if tests.is_dir() else Path(acceptance),
                                         identity, manifest["integrated"])
    checks = {record["check_id"]: record["result"] for record in records}
    guard = DevelopmentGuardrails(config)
    violations = []
    for name, digest in manifest["integrated"].items():
        if manifest["baseline"].get(name) != digest:
            violations.extend(guard.check_write(bundle / "integrated", name,
                              (bundle / "integrated" / name).read_text(encoding="utf-8"),
                              tests_only=guard.is_test_path(name)))
    # Deletions must be represented and checked as writes too.
    for name in manifest["baseline"].keys() - manifest["integrated"].keys():
        violations.extend(guard.check_write(bundle / "integrated", name, ""))
    checks["guardrails"] = "FAILED" if violations else "PASSED"
    criteria = specification.get("acceptance_criteria", [])
    trace = specification.get("traceability", {})
    checks["traceability"] = "PASSED" if criteria and all(row["id"] in trace for row in criteria) else "FAILED"
    review = None
    reference = acceptance / "review-reference.json"
    if reference.is_file():
        candidate = bundle / "review.json"
        if candidate.is_file():
            review = evaluate_review(json.loads(candidate.read_text(encoding="utf-8")),
                                     json.loads(reference.read_text(encoding="utf-8")), identity)
    return {"payload": {**identity, "checks": checks, "source": "GITHUB_ACTIONS_ACTUAL", "review_evaluation": review}, "records": records,
            "guardrail_violations": [{"rule": row.rule, "path": row.path} for row in violations]}


def sign_evaluation(evaluation, identity, key):
    """Called on a separate trusted runner, never in an application container."""
    if not key or len(key.encode()) < 32:
        raise ValueError("Independent evaluator signing key is unavailable")
    payload = evaluation["payload"]
    if any(payload.get(name) != value for name, value in identity.items()):
        raise ValueError("Evaluation identity differs from trusted workflow context")
    required = {"acceptance", "generated", "regression", "quality", "security", "definition_of_done", "dev"}
    records = evaluation["records"]
    if {row.get("check_id") for row in records} != required or len(records) != len(required):
        raise ValueError("Incomplete raw evaluator records")
    if any(any(row.get(name) != value for name, value in identity.items()) for row in records):
        raise ValueError("Raw evidence identity mismatch")
    for row in records:
        if row["result"] == "PASSED" and row.get("exit_code") != 0:
            raise ValueError("Check status is inconsistent with raw process exit")
        if payload["checks"].get(row["check_id"]) != row["result"]:
            raise ValueError("Check status differs from raw evidence")
    payload = {**payload, "raw_evidence_sha256": hashlib.sha256(
        json.dumps(evaluation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {"payload": payload, "mac": hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()}


def _producer_main():
    import argparse
    import os
    from pathlib import Path

    from .config import Config

    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("evaluate", "sign"))
    parser.add_argument("--config")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    identity = {name: os.environ["CI_" + name.upper()] for name in (
        "repository", "workflow", "commit", "evaluator_revision", "manifest_sha256", "spec_sha256")}
    if os.environ.get("CI_BUNDLE_SHA256"):
        identity["bundle_sha256"] = os.environ["CI_BUNDLE_SHA256"]
    identity.update(run_id=int(os.environ["GITHUB_RUN_ID"]), attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]))
    if args.operation == "evaluate":
        result = evaluate_bundle(Config.load(args.config), args.bundle, args.acceptance, identity)
    else:
        if args.input.stat().st_size > 1_000_000:
            raise ValueError("Oversized evaluator evidence")
        result = sign_evaluation(json.loads(args.input.read_text()), identity, os.environ.get("CI_SIGNING_KEY"))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True)


if __name__ == "__main__":
    _producer_main()
