import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from agentic_sdlc import experiments, intake, jira, llm
from agentic_sdlc.approval import LocalApprovalAuthority
from agentic_sdlc.cli import main
from agentic_sdlc.config import Config
from agentic_sdlc.development import agent_model_config, validate_provider
from agentic_sdlc.specifications import SpecificationStore, specification_digest


def approved_run(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_REVIEWER_KEY", "x" * 32)
    config = Config.load(overrides={"enterprise": {"approvers": {"reviewer": "TEST_REVIEWER_KEY"}}})
    store = SpecificationStore(tmp_path / "workflow.db")
    draft = {"issue": "CP-4", "status": "AWAITING_APPROVAL", "acceptance_criteria": [{"text": "Approved"}]}
    draft["spec_sha256"] = specification_digest(draft)
    run_id = store.create(draft)
    authority = LocalApprovalAuthority(config)
    store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None, authority)
    return store, run_id, authority


def test_issue_alias_and_persistent_effort(tmp_path, monkeypatch, capsys):
    store, run_id, _ = approved_run(tmp_path, monkeypatch)
    assert main(["dev", "CP-4", "--store", str(store.path)]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == run_id
    first = store.human_activity(run_id, "reviewer", "review")
    assert store.human_activity(run_id, "reviewer", "review") == first
    store.human_activity(run_id, "reviewer", "review", end=True, interrupt=True)
    store.human_activity(run_id, "reviewer", "manual-code")
    reopened = SpecificationStore(store.path)
    reopened.human_activity(run_id, "reviewer", "manual-code", end=True)
    effort = reopened.human_effort(run_id)
    assert set(effort["measured_seconds_by_kind"]) == {"manual-code"}
    assert effort["incomplete_sessions"] == 1


def test_correction_archives_results_and_rejects_stale_receipts(tmp_path, monkeypatch):
    store, run_id, authority = approved_run(tmp_path, monkeypatch)

    def generate(draft, attempt):
        return {"status": "READY_FOR_VERIFICATION", "spec_sha256": draft["spec_sha256"],
                "manifest_sha256": str(attempt) * 64}

    first = store.develop(run_id, authority, generate, 2, inputs={})
    old = first["manifest_sha256"]
    store.record_receipt(run_id, "review", {"manifest_sha256": old, "status": "BLOCKED"})
    correction = {"manifest_sha256": old, "source_attempt": 1}

    def remediate(draft, attempt, context):
        assert context["cycle"] == 1
        with pytest.raises(ValueError, match="obsolete"):
            store.record_receipt(run_id, "review", {"manifest_sha256": old})
        return generate(draft, attempt)

    second = store.develop(run_id, authority, generate, 2, inputs={}, correction=correction,
                           correction_limit=1, remediation_generate=remediate)
    assert second["manifest_sha256"] != old
    assert store.receipt(run_id, "review", optional=True) is None
    assert store.historical_receipts(run_id)[0]["record"]["status"] == "BLOCKED"
    exhausted = store.develop(run_id, authority, generate, 2, inputs={}, correction={
        "manifest_sha256": second["manifest_sha256"], "source_attempt": 2,
    }, correction_limit=1, remediation_generate=remediate)
    assert exhausted["reason"] == "Correction limit reached"


def test_authenticated_intake_and_exactly_one_specification(tmp_path, monkeypatch):
    key = "r" * 32
    monkeypatch.setenv("CAPSTONE_JIRA_RELAY_KEY", key)
    cfg = Config.load(overrides={"enterprise": {"intake": {"projects": ["CP"]}}})
    store = SpecificationStore(tmp_path / "workflow.db")
    payload = b'{"webhookEvent":"jira:issue_created","issue":{"key":"CP-9"}}'
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = hmac.new(key.encode(), f"delivery.{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="signature"):
        intake.accept(store, cfg, payload, delivery_id="delivery", timestamp=timestamp, signature="0" * 64)
    intake.accept(store, cfg, payload, delivery_id="delivery", timestamp=timestamp, signature=signature)
    draft = {"issue": "CP-9", "status": "BLOCKED"}
    draft["spec_sha256"] = specification_digest(draft)
    run_id = store.create(draft, delivery_id="delivery")
    assert store.create(draft, delivery_id="delivery") == run_id
    assert not store.intake_queue()


def test_experiment_has_equal_isolated_sources_and_no_invented_baseline(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("value = 1", encoding="utf-8")
    output = tmp_path / "experiment"
    plan = {"task_id": "fixture", "repetitions": 1, "starting_commit": "a" * 40,
            "spec_sha256": "b" * 64, "acceptance_sha256": "c" * 64, "repository": "fixture/repo",
            "complexity": "small", "rubric_version": "v1"}
    result = experiments.prepare(plan, source, output)
    assert len(result["trials"]) == 3
    (output / "01-agent_driven" / "app.py").write_text("value = 2", encoding="utf-8")
    assert (output / "01-human_baseline" / "app.py").read_text(encoding="utf-8") == "value = 1"
    assert all(row["comparison"] is None for row in experiments.summarize(output, None)["comparisons"])


def test_hosted_provider_is_explicit_and_records_measured_usage(monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "fixture-secret")
    config = Config.load(overrides={"enterprise": {
        "allowed_model_hosts": ["api.anthropic.com"],
        "agent_models": {"review": {"provider": "anthropic", "model": "fixture-model",
                                     "base_url": "https://api.anthropic.com/v1", "max_tokens": 200,
                                     "api_key_env": "TEST_ANTHROPIC_KEY", "input_cost_per_million": 1,
                                     "output_cost_per_million": 2}},
    }})
    review = agent_model_config(config, "review")
    with pytest.raises(ValueError, match="allow-provider"):
        validate_provider(review, False)
    validate_provider(review, True)

    def request(method, url, *, headers, body, timeout):
        assert method == "POST" and url == "https://api.anthropic.com/v1/messages"
        assert headers["x-api-key"] == "fixture-secret" and body["max_tokens"] == 200
        return {"model": "fixture-model", "content": [{"type": "text", "text": "{}"}],
                "usage": {"input_tokens": 3, "output_tokens": 4}}

    monkeypatch.setattr(llm, "request_json", request)
    client = llm.make_llm(review)
    assert client.complete("review", "test", "fixture") == "{}"
    assert client.usage()[0]["cost"] == 11 / 1_000_000
    assert client.usage()[0]["provider"] == "anthropic"


def test_jira_transition_requires_opt_in_and_scope(monkeypatch):
    monkeypatch.setenv("JIRA_USER", "fixture@example.test")
    monkeypatch.setenv("JIRA_TOKEN", "fixture-token")
    monkeypatch.setattr(jira, "request_json", lambda *args, **kwargs: pytest.fail("Unexpected external operation"))
    base = Config.load(overrides={"jira": {"base_url": "https://jira.example.test"}})
    with pytest.raises(ValueError, match="disabled"):
        jira.JiraClient(base).transition("CP-4", "review")
    enabled = base.with_overrides({"enterprise": {"jira_write": {
        "enabled": True, "projects": ["CP"], "transitions": {"review": "123"},
    }}})
    with pytest.raises(ValueError, match="scope"):
        jira.JiraClient(enabled).transition("OTHER-4", "review")
    observed = []
    monkeypatch.setattr(jira, "request_json", lambda method, url, **kwargs: observed.append((method, url, kwargs["body"])))
    assert jira.JiraClient(enabled).transition("CP-4", "review")["status"] == "TRANSITION_REQUESTED"
    assert observed == [("POST", "https://jira.example.test/rest/api/3/issue/CP-4/transitions",
                         {"transition": {"id": "123"}})]
