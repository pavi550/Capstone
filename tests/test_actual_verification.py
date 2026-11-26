"""Verifier policy, lifecycle and configured generation tests (services mocked)."""

import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest
import yaml

from agentic_sdlc import container_verifier, verification
from agentic_sdlc.approval import LocalApprovalAuthority
from agentic_sdlc.cli import main
from agentic_sdlc.config import Config
from agentic_sdlc.development import _manifest
from agentic_sdlc.orchestrator import Orchestrator
from agentic_sdlc.specifications import SpecificationStore, specification_digest


@pytest.fixture
def policy(monkeypatch):
    monkeypatch.setenv("APPROVAL_FIXTURE", "reviewer-fixture-key-of-at-least-32-bytes")
    monkeypatch.setenv("VERIFICATION_FIXTURE", "verifier-fixture-key-of-at-least-32-bytes")
    return Config.load(overrides={"enterprise": {
        "approvers": {"reviewer": "APPROVAL_FIXTURE"},
        "verifiers": {"verifier": "VERIFICATION_FIXTURE"},
        "container": {
            "image": "sha256:" + "a" * 64,
            "commands": {
                name: ["/usr/local/bin/python", "-m", "pytest", "-q",
                       "/acceptance" if name == "acceptance" else "/artifact"]
                for name in container_verifier.CHECK_IDS
            },
        },
    }})


def _artifact(tmp_path, cfg, *, real_model=False):
    store = SpecificationStore(tmp_path / "workflow.db")
    authority = LocalApprovalAuthority(cfg)
    draft = {"issue": "DEMO-1", "title": "Subtraction", "description": "Synthetic example",
             "acceptance_criteria": [{"text": "Return the difference"}], "status": "AWAITING_APPROVAL"}
    draft["spec_sha256"] = specification_digest(draft)
    run_id = store.create(draft)
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)
    source = tmp_path / "source"
    source.mkdir()
    orchestrator = Orchestrator(cfg)
    evidence = store.develop(
        run_id, authority, lambda spec, attempt: orchestrator.stage_development(
            spec, source, tmp_path / "out", run_id, attempt, allow_provider=real_model,
        ),
        1, inputs={
            "provider": cfg.get("llm.provider"), "model": cfg.get("llm.model"),
            "endpoint": cfg.get("llm.base_url"),
        },
    )
    assert evidence["status"] == "READY_FOR_VERIFICATION"
    return store, authority, run_id, evidence


@pytest.fixture
def mocked_docker(monkeypatch):
    monkeypatch.setattr(container_verifier.shutil, "which", lambda _name: "fixture-docker")
    monkeypatch.setattr(container_verifier.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=0, stdout="linux",
    ))
    calls = []

    def capture(command, *, env, timeout, cleanup):
        calls.append((command, env, timeout, cleanup))
        return 0, {"sha256": "a" * 64, "bytes": 1}, {"sha256": "b" * 64, "bytes": 0}

    monkeypatch.setattr(container_verifier, "_capture", capture)
    return calls


def test_docker_verifier_uses_read_only_mounts_and_does_not_forward_credentials(
    tmp_path, policy, mocked_docker, monkeypatch,
):
    monkeypatch.setenv("SECRET_FIXTURE", "do-not-forward")
    artifact, acceptance = tmp_path / "artifact", tmp_path / "acceptance"
    artifact.mkdir()
    acceptance.mkdir()
    (artifact / "app.py").write_text("VALUE = 2", encoding="utf-8")
    (acceptance / "test_fixture.py").write_text("def test_fixture(): pass", encoding="utf-8")
    results = container_verifier.DockerVerifier(policy).run(
        artifact, acceptance, {"run_id": "fixture"}, _manifest(artifact),
    )
    assert {receipt["check_id"] for receipt in results} == container_verifier.CHECK_IDS
    for receipt, (command, env, _, cleanup) in zip(results, mocked_docker, strict=True):
        assert "SECRET_FIXTURE" not in env
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--pull") + 1] == "never"
        assert "--read-only" in command and "--cap-drop" in command and "--user" in command
        mounts = [value for value in command if value.startswith("type=bind,")]
        assert len(mounts) == (2 if receipt["check_id"] == "acceptance" else 1)
        assert all(",readonly," in mount for mount in mounts)
        assert cleanup[-1] == command[command.index("--name") + 1]


def test_unavailable_engine_fails_closed(tmp_path, policy, mocked_docker, monkeypatch):
    artifact, acceptance = tmp_path / "artifact", tmp_path / "acceptance"
    artifact.mkdir()
    acceptance.mkdir()
    monkeypatch.setattr(container_verifier.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=1, stdout="",
    ))
    with pytest.raises(ValueError, match="no code was executed"):
        container_verifier.DockerVerifier(policy).run(artifact, acceptance, {}, {})
    assert not mocked_docker


@pytest.mark.parametrize("exit_code", [0, 1])
def test_actual_receipt_gates_and_bound_review(tmp_path, policy, mocked_docker, monkeypatch, exit_code, capsys):
    store, authority, run_id, evidence = _artifact(tmp_path, policy)
    acceptance = tmp_path / "trusted"
    acceptance.mkdir()
    (acceptance / "fixture.txt").write_text("trusted", encoding="utf-8")

    def capture(*_args, **_kwargs):
        return exit_code, {"sha256": "a" * 64, "bytes": 1}, {"sha256": "b" * 64, "bytes": 0}

    monkeypatch.setattr(container_verifier, "_capture", capture)
    config = tmp_path / "policy.yaml"
    config.write_text(yaml.safe_dump(policy.data), encoding="utf-8")
    common = ["--store", str(store.path), "--run-id", run_id, "--config", str(config)]
    assert main(["dev", "testing", *common, "--actor", "verifier", "--acceptance-dir", str(acceptance)]) == (
        2 if exit_code else 0
    )
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["environment"] == "LOCAL_ACTUAL"
    assert all(check["exit_code"] == exit_code for check in outcome["checks"])
    first_pass = store.receipt(run_id, "first_pass_score")
    assert first_pass["delivery_eligibility"] == ("BLOCKED" if exit_code else "PENDING")
    assert main(["dev", "score", "--store", str(store.path), "--run-id", run_id,
                 "--config", str(config), "--report-dir", str(tmp_path / "report")]) == 0
    score = json.loads(capsys.readouterr().out)
    assert score["status"] == "PROVISIONAL"
    assert score["earned_points"] == (0 if exit_code else 7)
    assert score["delivery_eligibility"] == ("BLOCKED" if exit_code else "PENDING")
    if exit_code:
        with pytest.raises(ValueError, match="mandatory gate"):
            verification.review(store, run_id, authority, policy)
        return
    reviewed = verification.review(store, run_id, authority, policy)
    verification.approve_review(store, run_id, authority, "reviewer", reviewed["review_sha256"])
    assert main(["dev", "validate", *common]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "LOCAL_DEV_VALIDATED"
    assert store.receipt(run_id, "final_score")["status"] == "PROVISIONAL"
    assert store.receipt(run_id, "first_pass_score") == first_pass
    manifest = os.path.join(evidence["artifact_dir"], "integrated", evidence["code_files"][0])
    with open(manifest, "a", encoding="utf-8") as stream:
        stream.write("# modified")
    with pytest.raises(ValueError, match="artifacts changed"):
        verification.validate(store, run_id, authority)


def test_configured_generation_tracks_usage_and_rejects_synthetic_verifier(tmp_path, policy, monkeypatch):
    monkeypatch.setenv("MODEL_FIXTURE_KEY", "loopback-fixture")
    cfg = Config.load(overrides={**policy.data, "llm": {
        "provider": "openai", "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "MODEL_FIXTURE_KEY", "model": "fixture-model", "max_tokens": 40,
    }})

    def request(method, url, *, body, **_):
        assert method == "POST" and url.startswith("http://127.0.0.1:")
        assert body["max_tokens"] == 40
        system = body["messages"][0]["content"]
        name = "tests/test_app.py" if "Test" in system.splitlines()[0] else "app.py"
        return {"model": "fixture-reported", "usage": {"prompt_tokens": 8, "completion_tokens": 4},
                "choices": [{"message": {"content": json.dumps({
                    "files": [{"path": name, "content": "VALUE = 1"}],
                })}}]}

    monkeypatch.setattr("agentic_sdlc.llm.request_json", request)
    store, authority, run_id, artifact = _artifact(tmp_path, cfg, real_model=True)
    assert artifact["execution_mode"] == "CONFIGURED_LOCAL_LLM"
    assert len(artifact["provider_usage"]) == 2
    assert all(row["cost"] is None and row["input_tokens"] == 8 for row in artifact["provider_usage"])
    with pytest.raises(ValueError, match="Synthetic verification"):
        verification.record_verification(store, run_id, authority, "verifier", {})


def test_process_observer_records_real_exit_and_stream_digests():
    command = [sys.executable, "-I", "-c", "import sys; print('fixture'); sys.stderr.write('warn'); sys.exit(3)"]
    status, stdout, stderr = container_verifier._capture(
        command, env=container_verifier._environment(), timeout=10, cleanup=[],
    )
    fixture = b"fixture\r\n" if os.name == "nt" else b"fixture\n"
    assert status == 3
    assert stdout == {"sha256": hashlib.sha256(fixture).hexdigest(), "bytes": len(fixture)}
    assert stderr == {"sha256": hashlib.sha256(b"warn").hexdigest(), "bytes": 4}


def test_disposable_unified_local_workflow_corrects_and_resumes(tmp_path, policy, monkeypatch, capsys):
    from agentic_sdlc.llm import OpenAICompatibleLLM

    monkeypatch.setenv("UNIFIED_FIXTURE_MODEL", "fixture")
    source, acceptance, references = (tmp_path / name for name in ("source", "acceptance", "references"))
    for directory in (source, acceptance, references):
        directory.mkdir()
    (acceptance / "checks.py").write_text("def test_fixture(): pass", encoding="utf-8")
    issues = tmp_path / "issues.json"
    issues.write_text(json.dumps([{"key": "DEMO-42", "fields": {"summary": "Fixture",
        "description": "Acceptance Criteria:\n- Return the expected value"}}]), encoding="utf-8")
    config = tmp_path / "policy.json"
    cfg = policy.with_overrides({"jira": {"issues_file": str(issues)}, "llm": {
        "provider": "openai", "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "UNIFIED_FIXTURE_MODEL", "model": "fixture",
    }, "enterprise": {"delivery_policy": {"local_review": {
        "required": True, "reference_dir": str(references),
    }}}})
    config.write_text(json.dumps(cfg.data), encoding="utf-8")
    generated = 0

    def complete(_self, task, _system, _user, context=None):
        nonlocal generated
        if task == "requirement":
            return json.dumps({key: [] for key in ("functional", "non_functional", "security", "dependencies", "ambiguities", "missing_mandatory", "candidate_criteria")})
        if task == "review":
            return json.dumps({"findings": [{"severity": "high", "file": "app.py", "message": "Fixture mismatch"}]
                               if generated == 1 else []})
        if task == "test":
            return json.dumps({"files": [{"path": "tests/test_app.py", "content": "def test_fixture(): pass\n"}]})
        generated += 1
        return json.dumps({"files": [{"path": "app.py", "content": f"VALUE = {generated}\n"}]})

    monkeypatch.setattr(OpenAICompatibleLLM, "complete", complete)
    monkeypatch.setattr(container_verifier.shutil, "which", lambda _name: "fixture-docker")
    monkeypatch.setattr(container_verifier.subprocess, "run", lambda command, **_kw: SimpleNamespace(
        returncode=0, stdout="" if "ps" in command else "linux",
    ))
    monkeypatch.setattr(container_verifier, "_capture", lambda *_args, **_kwargs: (
        0, {"sha256": "a" * 64, "bytes": 0}, {"sha256": "b" * 64, "bytes": 0},
    ))

    evaluate = verification.local_review_evaluation

    def supply_reference(store, run_id, authority, config, actor):
        candidate = store.receipt(run_id, "review")
        reference = {key: candidate[key] for key in ("manifest_sha256", "spec_sha256")}
        reference.update(findings_sha256=verification._digest(candidate),
                         defects=["fixture-defect"] if generated == 1 else [],
                         labels={row["id"]: {"adjudication": "confirmed", "defect_id": "fixture-defect"}
                                 for row in candidate["findings"] if row["origin"] == "ai"})
        (references / (reference["manifest_sha256"] + ".json")).write_text(json.dumps(reference), encoding="utf-8")
        return evaluate(store, run_id, authority, config, actor)

    monkeypatch.setattr(verification, "local_review_evaluation", supply_reference)
    db = tmp_path / "unified.db"
    assert main(["dev", "workflow-start", "DEMO-42", "--store", str(db), "--config", str(config),
                 "--repo", str(source), "--out", str(tmp_path / "out"), "--acceptance-dir", str(acceptance),
                 "--actor", "verifier", "--allow-provider"]) == 0
    initial = json.loads(capsys.readouterr().out)
    assert initial["stage"] == "requirements"
    assert initial["status"] == "WAITING_FOR_APPROVAL"
    run_id = initial["run_id"]
    store = SpecificationStore(db)
    authority = LocalApprovalAuthority(cfg)
    store.decide(run_id, initial["specification_sha256"], "APPROVED", "reviewer", None, authority)
    resume = ["dev", "workflow-resume", "--store", str(db), "--run-id", run_id, "--allow-provider"]
    assert main(resume) == 0
    paused = json.loads(capsys.readouterr().out)
    assert (paused["stage"], paused["status"]) == ("review", "WAITING_FOR_APPROVAL")
    assert generated == 2
    prior = [row["record"] for row in store.historical_receipts(run_id) if row["kind"] == "local_review_evidence"]
    assert prior[0]["payload"]["metrics"]["ai"]["recall"] == 1.0
    assert store.receipt(run_id, "local_review_evidence")["payload"]["attempt_id"] == 2
    verification.approve_review(store, run_id, authority, "reviewer", paused["review_sha256"])
    assert main(resume) == 0
    finished = json.loads(capsys.readouterr().out)
    assert (finished["stage"], finished["status"]) == ("delivery", "LOCAL_DEV_VALIDATED")
    assert store.receipt(run_id, "final_score")
    assert finished["report"]["recorded_receipts"]["final_score"]
    assert generated == 2
