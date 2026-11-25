"""Offline acceptance tests for approval-gated and isolated development."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from agentic_sdlc import verification
from agentic_sdlc.agents import apply_changes
from agentic_sdlc.approval import LocalApprovalAuthority
from agentic_sdlc.cli import main
from agentic_sdlc.config import Config
from agentic_sdlc.development import DevelopmentGuardrails, stage
from agentic_sdlc.orchestrator import Orchestrator
from agentic_sdlc.specifications import SpecificationStore, specification_digest


@pytest.fixture
def draft():
    data = {
        "issue": "CP-101", "title": "Synthetic subtraction", "description": "Synthetic offline test",
        "status": "AWAITING_APPROVAL", "acceptance_criteria": [{"text": "Returns the difference"}],
    }
    return {**data, "spec_sha256": specification_digest(data)}


@pytest.fixture
def approval_config(monkeypatch, tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("enterprise:\n  approvers:\n    reviewer: FIXTURE_APPROVAL_KEY\n", encoding="utf-8")
    monkeypatch.setenv("FIXTURE_APPROVAL_KEY", "synthetic-fixture-key-of-at-least-32-bytes")
    return path


def test_authenticated_approval_unlocks_offline_development_and_resume(
    draft, approval_config, tmp_path, capsys,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".env").write_text("FIXTURE=private", encoding="utf-8")
    store = SpecificationStore(tmp_path / "spec.db")
    run_id = store.create(draft)
    args = [
        "--store", str(store.path), "--run-id", run_id, "--repo", str(source),
        "--out", str(tmp_path / "artifacts"), "--config", str(approval_config),
    ]
    assert main(["dev", "run", *args]) == 2
    assert not (tmp_path / "artifacts").exists()
    assert main(["dev", "approve", "--store", str(store.path), "--run-id", run_id,
                 "--spec-sha256", draft["spec_sha256"], "--config", str(approval_config),
                 "--actor", "reviewer"]) == 0
    capsys.readouterr()
    assert main(["dev", "run", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "READY_FOR_VERIFICATION"
    assert result["manifest_sha256"]
    assert {item.name for item in source.iterdir()} == {".env", "existing.py"}
    assert main(["dev", "resume", *args]) == 0
    assert json.loads(capsys.readouterr().out) == result
    assert len(store.get(run_id)["development_attempts"]) == 1
    artifact = Path(result["artifact_dir"])
    (artifact / "integrated" / "existing.py").write_text("VALUE = 2", encoding="utf-8")
    assert main(["dev", "resume", *args]) == 2
    assert "artifacts changed" in capsys.readouterr().err


def test_unsigned_or_tampered_approval_cannot_unlock(draft, approval_config, tmp_path, monkeypatch):
    store = SpecificationStore(tmp_path / "spec.db")
    run_id = store.create(draft)
    authority = LocalApprovalAuthority(Config.load(approval_config))
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None)

    def unexpected(*_args):
        pytest.fail("unauthenticated run executed")

    with pytest.raises(ValueError, match="verified local approval"):
        store.develop(run_id, authority, unexpected, 2, inputs={})
    with pytest.raises(ValueError, match="not authenticated"):
        store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)

    run_id = store.create(draft)
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)
    monkeypatch.setenv("FIXTURE_APPROVAL_KEY", "different-synthetic-key-of-at-least-32-bytes")
    with pytest.raises(ValueError, match="verified local approval"):
        store.develop(run_id, authority, unexpected, 2, inputs={})


def test_agents_run_concurrently_in_separate_workspaces(draft, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    barrier = Barrier(2, timeout=5)

    def coding(_agent, _work, repo):
        (repo / "app.py").write_text("VALUE = 1", encoding="utf-8")
        barrier.wait()
        assert not (repo / "test_app.py").exists()
        return ["app.py"], []

    def testing(_agent, _work, repo):
        (repo / "test_app.py").write_text("fixture", encoding="utf-8")
        barrier.wait()
        assert not (repo / "app.py").exists()
        return ["test_app.py"], []

    monkeypatch.setattr("agentic_sdlc.development.CodingAgent.implement", coding)
    monkeypatch.setattr("agentic_sdlc.development.TestAgent.write_tests", testing)
    result = stage(Orchestrator(Config.load()), draft, source, tmp_path / "out", "fixture", 1)
    assert result["status"] == "READY_FOR_VERIFICATION"
    assert set(result["timings_ms"]) == {"code", "testcase", "parallel", "integration"}


def test_interruption_preserves_attempt_and_resume_starts_next_attempt(draft, approval_config, tmp_path):
    store = SpecificationStore(tmp_path / "spec.db")
    run_id = store.create(draft)
    authority = LocalApprovalAuthority(Config.load(approval_config))
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)

    def interrupted(*_args):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        store.develop(run_id, authority, interrupted, 2, inputs={})
    assert store.get(run_id)["development_attempts"][0]["status"] == "RUNNING"
    assert store.get(run_id)["approval_wait_seconds"] is not None

    result = SpecificationStore(store.path).develop(
        run_id, authority, lambda _draft, attempt: {"status": "READY_FOR_VERIFICATION", "attempt": attempt},
        2, inputs={},
    )
    assert result["attempt"] == 2
    assert [row["status"] for row in store.get(run_id)["development_attempts"]] == [
        "INTERRUPTED", "READY_FOR_VERIFICATION",
    ]


def test_running_stage_rejects_duplicate_and_records_cancellation(draft, approval_config, tmp_path):
    store = SpecificationStore(tmp_path / "spec.db")
    run_id = store.create(draft)
    authority = LocalApprovalAuthority(Config.load(approval_config))
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)
    started, release = Event(), Event()

    def generate(*_args):
        started.set()
        assert release.wait(5)
        return {"status": "READY_FOR_VERIFICATION"}

    def unexpected(*_args):
        pytest.fail("duplicate execution passed the run lock")

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(store.develop, run_id, authority, generate, 2, inputs={})
        try:
            assert started.wait(5)
            with pytest.raises(ValueError, match="locked"):
                store.develop(run_id, authority, unexpected, 2, inputs={})
            assert store.cancel(run_id)["status"] == "CANCELLED"
        finally:
            release.set()
        assert future.result()["status"] == "CANCELLED"
    with pytest.raises(ValueError, match="cancelled"):
        store.develop(run_id, authority, unexpected, 2, inputs={})


@pytest.mark.parametrize("passed", [True, False])
def test_independent_gate_and_review_approval_are_required(draft, tmp_path, monkeypatch, passed):
    monkeypatch.setenv("APPROVAL_FIXTURE", "reviewer-fixture-key-of-at-least-32-bytes")
    monkeypatch.setenv("VERIFICATION_FIXTURE", "verifier-fixture-key-of-at-least-32-bytes")
    cfg = Config.load(overrides={"enterprise": {
        "approvers": {"reviewer": "APPROVAL_FIXTURE"},
        "verifiers": {"verifier": "VERIFICATION_FIXTURE"},
    }})
    authority = LocalApprovalAuthority(cfg)
    store = SpecificationStore(tmp_path / "trusted.db")
    source = tmp_path / "source"
    source.mkdir()
    run_id = store.create(draft)
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)
    orchestrator = Orchestrator(cfg)
    artifact = store.develop(
        run_id, authority, lambda submitted, attempt: orchestrator.stage_development(
            submitted, source, tmp_path / "out", run_id, attempt,
        ), 2, inputs={},
    )
    attestation = {
        "run_id": run_id, "spec_sha256": draft["spec_sha256"],
        "manifest_sha256": artifact["manifest_sha256"], "environment": "OFFLINE_SIMULATION",
        "checks": {name: name != "acceptance" or passed for name in verification.CHECKS},
    }
    with pytest.raises(ValueError, match="distinct"):
        verification.record_verification(store, run_id, authority, "reviewer", attestation)
    verification.record_verification(store, run_id, authority, "verifier", attestation)
    if not passed:
        with pytest.raises(ValueError, match="mandatory gate"):
            verification.review(store, run_id, authority, cfg)
        return
    result = verification.review(store, run_id, authority, cfg)
    assert result["status"] == "WAITING_REVIEW_APPROVAL"
    with pytest.raises(ValueError, match="Missing review_approval"):
        verification.validate(store, run_id, authority)
    with pytest.raises(ValueError, match="Review digest"):
        verification.approve_review(store, run_id, authority, "reviewer", "stale")
    approved = verification.approve_review(store, run_id, authority, "reviewer", result["review_sha256"])
    assert verification.approve_review(store, run_id, authority, "reviewer", result["review_sha256"]) == approved
    assert verification.validate(store, run_id, authority)["status"] == "OFFLINE_VALIDATED"


def test_offline_guardrails_reject_protected_files_before_write(tmp_path):
    guards = DevelopmentGuardrails(Config.load())
    paths = [".env.local", ".github/workflows/ci.yml", "evaluation/scoring.yaml", "tests/test_trusted.py"]
    written, denied = apply_changes(
        tmp_path, [{"path": path, "content": "synthetic"} for path in paths], guards,
    )
    assert written == []
    assert {violation.path for violation in denied} == set(paths)
    assert list(tmp_path.iterdir()) == []
