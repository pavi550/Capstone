import hashlib
import hmac
import json
from datetime import UTC, datetime
from zipfile import ZipFile

import pytest

from agentic_sdlc import intake, lifecycle, remote_dev, scenarios
from agentic_sdlc.config import Config
from agentic_sdlc.delivery_evidence import (
    measurements,
    registry,
    validate_ci,
    verify_evaluator_archive,
)
from agentic_sdlc.provider_budget import reconcile, reserve
from agentic_sdlc.specifications import SpecificationStore, specification_digest


def stored_run(tmp_path):
    store = SpecificationStore(tmp_path / "store.db")
    draft = {"issue": "FIX-1", "status": "BLOCKED"}
    return store, store.create({**draft, "spec_sha256": specification_digest(draft)})


def test_journal_persists_cooperative_cancellation(tmp_path):
    store, run_id = stored_run(tmp_path)

    def cancel():
        assert store.cancel(run_id)["status"] == "CANCELLING"
        lifecycle.checkpoint()

    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run_id, "testing", cancel)
    assert store.lifecycle_stages(run_id)[0]["state"] == "CANCELLED"
    with pytest.raises(lifecycle.StageCancelled):
        lifecycle.execute(store, run_id, "validate", lambda: pytest.fail("Cancelled stage executed"))


def test_budget_counts_pending_requests_and_unknown_usage(tmp_path):
    store, run_id = stored_run(tmp_path)
    config = Config.load(overrides={"enterprise": {"budgets": {"run": {"max_total_tokens": 12}}}})
    context = (store, run_id, "run", 1)
    first = reserve(context, config, "code", 5, 5, (None, None))
    with pytest.raises(ValueError, match="budget exhausted"):
        reserve(context, config, "test", 2, 2, (None, None))
    reconcile(first, 1, 2, status="RECONCILED")
    second = reserve(context, config, "test", 3, 3, (None, None))
    reconcile(second)
    with pytest.raises(ValueError, match="budget exhausted"):
        reserve(context, config, "code", 2, 2, (None, None))
    assert [row["state"] for row in store.provider_requests(run_id)] == ["RECONCILED", "FAILED"]


def test_rubric_registry_has_all_points_and_checks_measured_denominators():
    config = Config.load()
    assert sum(row["maximum_points"] for row in registry(config).values()) == 100
    row = {"method": "deterministic", "passed": 1, "measured": 2, "reason": "independent review",
           "sources": ["a" * 64]}
    document = {"rubric_version": "1.0", "criteria": {"regression": row}}
    assert measurements(config, document)["regression"]["measured"] == 2
    row["measured"] = 0
    with pytest.raises(ValueError, match="denominator"):
        measurements(config, document)


def test_ci_rejects_artifact_and_evaluator_mismatch():
    stamp = datetime.now(UTC)
    policy = {"required": True, "repository": "owner/repo", "workflow": "trusted.yml", "jobs": ["test"],
              "evaluator_revision": "a" * 40, "artifacts": ["proof"]}
    identity = {"repository": "owner/repo", "workflow": ".github/workflows/trusted.yml", "run_id": 2,
                "attempt": 1, "commit": "b" * 40}
    binding = {"manifest_sha256": "c" * 64}
    archive = {"name": "proof", "digest": "sha256:" + "d" * 64, "expired": False}
    fetched = {**identity, "conclusion": "success", "run_artifact_metadata": [archive],
               "jobs": [{"name": "test", "status": "completed", "conclusion": "success", "completed_at": stamp.isoformat()}]}
    statement = {**identity, **binding, "evaluator_revision": policy["evaluator_revision"],
                 "verified_archives": {"proof": archive["digest"]}}
    validate_ci(policy, fetched, binding, statement, now=stamp)
    archive["expired"] = True
    with pytest.raises(ValueError, match="archive"):
        validate_ci(policy, fetched, binding, statement, now=stamp)
    with pytest.raises(ValueError, match="revision"):
        validate_ci(policy, fetched, binding, {**statement, "evaluator_revision": "unknown"}, now=stamp)


def test_remote_read_only_recovery_does_not_repeat_put(monkeypatch):
    monkeypatch.setenv("TEST_BROKER_TOKEN", "synthetic")
    config = Config.load(overrides={"enterprise": {"remote_dev": {
        "enabled": True, "tier": "DEV", "environment": "dev-fixture", "allowed_environments": ["dev-fixture"],
        "endpoint": "https://broker.example.test", "token_env": "TEST_BROKER_TOKEN",
    }}})
    methods = []
    binding = {"manifest_sha256": "m"}

    def request(method, url, **kwargs):
        methods.append(method)
        identity = {**binding, "environment": "dev-fixture", "tier": "DEV"}
        return {"binding": identity, "deployment_id": url.rsplit("/", 1)[1], "status": "PASSED", "checks": {
            key: "PASSED" for key in ("prepare", "build", "deploy", "start", "health", "smoke", "cleanup")}}

    monkeypatch.setattr(remote_dev, "request_json", request)
    assert remote_dev.run(config, binding, create=False)["status"] == "PASSED"
    assert methods == ["GET"]


def test_oauth_webhook_requires_authorization_and_project_scope(monkeypatch):
    from agentic_sdlc import httpclient

    monkeypatch.setenv("TEST_OAUTH", "synthetic")
    config = Config.load(overrides={"enterprise": {"intake": {"projects": ["XYZ"], "webhook": {
        "authentication": "oauth2", "cloud_id": "00000000-0000-0000-0000-000000000001",
        "callback": "https://relay.example.test/jira", "allow_write": True, "token_env": "TEST_OAUTH",
    }}}})
    with pytest.raises(ValueError, match="explicit authorization"):
        intake.webhook(config, "register")

    def request(method, url, **kwargs):
        assert method == "POST" and url.endswith("/rest/api/3/webhook")
        assert kwargs["body"]["webhooks"][0]["jqlFilter"] == "project in (XYZ)"
        return {"webhookRegistrationResult": [{"createdWebhookId": 12}]}

    monkeypatch.setattr(httpclient, "request_json", request)
    assert intake.webhook(config, "register", authorized=True)["id"] == 12


def test_scenarios_keep_evaluator_only_files_outside_workspace(tmp_path):
    workspace, evaluator = tmp_path / "trial", tmp_path / "trusted"
    scenarios.materialize("bug-fix", workspace, evaluator)
    assert (evaluator / "ground_truth.json").is_file()
    assert not list(workspace.rglob("ground_truth.json"))
    assert not list(workspace.rglob("test_acceptance.py"))
    result = scenarios.evaluate_guardrails(Config.load(), tmp_path / "guard")
    assert result["metrics"]["aggregate"]["runtime"]["blocked"] == 6


def test_archive_requires_independent_evaluator_signature(tmp_path, monkeypatch):
    key = "a" * 32
    monkeypatch.setenv("TEST_EVALUATOR_KEY", key)
    policy = {"evaluator_key_env": "TEST_EVALUATOR_KEY", "checks": ["acceptance"]}
    config = Config.load(overrides={"enterprise": {"delivery_policy": {"remote_ci": policy}}})
    payload = {"checks": {"acceptance": "PASSED"}, "manifest_sha256": "b" * 64}
    mac = hmac.new(key.encode(), json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                   hashlib.sha256).hexdigest()
    target = tmp_path / "signed.zip"
    with ZipFile(target, "w") as archive:
        archive.writestr("attestation.json", json.dumps({"payload": payload, "mac": mac}))
    assert verify_evaluator_archive(config, policy, target, payload) == payload
    monkeypatch.setenv("TEST_EVALUATOR_KEY", "c" * 32)
    with pytest.raises(ValueError, match="signature"):
        verify_evaluator_archive(config, policy, target, payload)


def test_intake_failures_enter_dead_letter_without_reprocessing(tmp_path):
    store, _ = stored_run(tmp_path)
    store.enqueue_issue("event", "digest", "FIX-1")
    store.fail_intake("event", "RuntimeError", 2)
    assert len(store.intake_queue()) == 1
    store.fail_intake("event", "RuntimeError", 2)
    assert store.intake_queue() == []
    assert store.dead_letters()[0]["attempts"] == 2
