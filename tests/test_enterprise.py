"""Regression tests for the enterprise read and independent evidence paths."""

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from agentic_sdlc import confluence, jira
from agentic_sdlc.cli import main
from agentic_sdlc.config import Config
from agentic_sdlc.evaluation import compare, score
from agentic_sdlc.events import EventLog, summarize
from agentic_sdlc.httpclient import HttpError
from agentic_sdlc.metrics import guardrail_effectiveness, independently_reviewed
from agentic_sdlc.orchestrator import Graph
from agentic_sdlc.specifications import SpecificationStore, specification_digest
from agentic_sdlc.state import RunState


def artifact(tmp_path):
    payload = b"independent check results"
    (tmp_path / "check.json").write_bytes(payload)
    return {"path": "check.json", "sha256": hashlib.sha256(payload).hexdigest()}


def test_partial_evidence_and_failed_mandatory_gate(tmp_path):
    ref = artifact(tmp_path)
    assessment = {
        "evaluator_version": "fixture-1",
        "criteria": {"functional_tests": {
            "ratio": 0.9, "method": "18 of 20 tests", "reason": "Two failures", "evidence": [ref],
        }},
        "gates": {"independent_tests": {"passed": False, "evidence": [ref]}},
    }
    rubric = Config.load().get("evaluation.rubric")
    result = score(rubric, assessment, tmp_path)
    assert result["earned_points"] == 4.5
    assert result["evaluated_maximum"] == 5
    assert result["configured_maximum"] == 100
    assert result["evidence_completeness"] == 0.05
    assert result["delivery_eligibility"] == "BLOCKED"
    assert result["status"] == "PROVISIONAL"
    (tmp_path / "check.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        score(rubric, assessment, tmp_path)


def test_scoring_requires_terminal_gates_and_valid_rubric(tmp_path):
    ref = artifact(tmp_path)
    rubric = {"version": "1", "mandatory_gates": ["ci"], "criteria": {"code": {"correctness": 100}}}
    evaluation = {"evaluator_version": "1", "criteria": {
        "correctness": {"ratio": 1, "method": "acceptance", "reason": "passed", "evidence": [ref]},
    }}
    assert score(rubric, evaluation, tmp_path)["delivery_eligibility"] == "PENDING"
    evaluation["gates"] = {"ci": {"passed": True, "evidence": [ref]}}
    assert score(rubric, evaluation, tmp_path)["delivery_eligibility"] == "ELIGIBLE"
    rubric["criteria"]["code"]["correctness"] = 99
    with pytest.raises(ValueError, match="sum to 100"):
        score(rubric, evaluation, tmp_path)


def test_comparison_preserves_regression_and_missing_values():
    baseline = {
        "mode": "HUMAN_BASELINE", "task_id": "fixture", "repository": "sample",
        "starting_commit": "abc", "spec_sha256": "a" * 64, "acceptance_sha256": "b" * 64,
        "delivery_seconds": 10, "human_seconds": 0,
    }
    actual = {**baseline, "mode": "AGENT_DRIVEN", "delivery_seconds": 20, "human_seconds": 1}
    result = compare(baseline, actual)
    assert result["delivery_time_reduction_pct"] == -100
    assert result["delivery_speedup"] == 0.5
    assert result["human_effort_saved_pct"] is None
    assert result["cost_reduction_pct"] is None
    with pytest.raises(ValueError, match="starting_commit"):
        compare(baseline, {**actual, "starting_commit": "different"})


def test_cloud_jira_pagination_retry_and_adf(monkeypatch):
    calls = []
    adf = {
        "type": "doc", "content": [
            {"type": "heading", "content": [{"type": "text", "text": "Acceptance criteria"}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": "Returns the difference"},
                ]}]},
            ]},
        ],
    }
    pages = iter([
        HttpError(429, "synthetic private body"),
        {"issues": [{"key": "CP-1"}], "nextPageToken": "next", "isLast": False},
        {"issues": [{"key": "CP-2", "fields": {"description": adf}}], "isLast": True},
    ])

    def get(method, url, **_):
        assert method == "GET"
        calls.append(parse_qs(urlsplit(url).query))
        reply = next(pages)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setenv("JIRA_USER", "fixture-user")
    monkeypatch.setenv("JIRA_TOKEN", "fixture-token")
    monkeypatch.setattr(jira, "request_json", get)
    monkeypatch.setattr(jira, "sleep", lambda _: None)
    cfg = Config.load(overrides={"jira": {
        "base_url": "https://jira.example.test", "search_path": "/rest/api/3/search/jql",
    }})
    results = jira.JiraClient(cfg).search('project = "CP"')
    assert calls[0] == calls[1]
    assert calls[2]["nextPageToken"] == ["next"]
    assert jira.issue_to_requirement(results[-1]).acceptance_criteria == ["Returns the difference"]


def test_confluence_context_keeps_provenance_and_bounds_content(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_EMAIL", "example@example.test")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "fixture-token")
    body = "<p>fixture</p>"

    def get(method, url, **_):
        assert (method, url) == ("GET", "https://wiki.example.test/wiki/api/v2/pages/12?body-format=storage")
        return {"id": "12", "title": "Fixture", "version": {"number": 3},
                "body": {"storage": {"value": body}}}

    monkeypatch.setattr(confluence, "request_json", get)
    cfg = Config.load(overrides={"confluence": {"base_url": "https://wiki.example.test", "max_chars": 3}})
    result = confluence.ConfluenceClient(cfg).read_page("12")
    assert result["untrusted_storage"] == "<p>"
    assert result["version"] == 3
    assert result["truncated"]
    assert result["sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_offline_spec_and_score_commands(tmp_path, capsys):
    issues = tmp_path / "issues.json"
    issues.write_text(json.dumps([{"key": "CP-1", "fields": {
        "summary": "Synthetic", "description": "Acceptance criteria:\n- one",
    }}]), encoding="utf-8")
    config = tmp_path / "fixture.yaml"
    config.write_text(f"jira:\n  issues_file: {json.dumps(str(issues))}\n", encoding="utf-8")
    draft = tmp_path / "draft.json"
    assert main(["dev", "spec", "CP-1", "--config", str(config), "--output", str(draft)]) == 0
    spec = json.loads(draft.read_text(encoding="utf-8"))
    assert spec["status"] == "AWAITING_APPROVAL"
    assert spec["acceptance_criteria"][0]["id"] == "CP-1-AC-1"
    capsys.readouterr()

    assessment = tmp_path / "assessment.json"
    assessment.write_text('{"evaluator_version":"fixture-1"}', encoding="utf-8")
    reports = tmp_path / "report"
    assert main(["dev", "score", "--assessment", str(assessment), "--evidence-dir", str(tmp_path),
                 "--report-dir", str(reports)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PROVISIONAL"
    assert {p.name for p in reports.iterdir()} == {"score.json", "score.csv", "score.md"}


def test_productivity_index_uses_a_separate_comparable_quality_score():
    record = {
        "task_id": "fixture", "repository": "sample", "starting_commit": "abc",
        "spec_sha256": "a" * 64, "acceptance_sha256": "b" * 64, "currency": "USD",
        "delivery_seconds": 100, "human_seconds": 50, "total_cost": 10,
        "deliverable_quality": {"score": 90, "rubric_version": "deliverable-only-1"},
    }
    baseline = {**record, "mode": "HUMAN_BASELINE"}
    actual = {**record, "mode": "AGENT_ASSISTED"}
    weights = Config.load().get("evaluation.index_weights")
    index = compare(baseline, actual, weights)["overall_productivity_index"]
    assert index["score"] == 66
    assert index["components"]["human_effort"] == 50
    assert compare(baseline, {**actual, "human_seconds": None}, weights)["overall_productivity_index"] is None


def test_failed_node_records_timing_without_claiming_completion(tmp_path):
    graph = Graph()

    def fail(_state):
        raise RuntimeError("fixture failure")

    graph.add_node("fixture", fail)
    state = RunState(run_id="fixture", out_dir=tmp_path, repo_path=None, requirement_sources=[])
    events = EventLog(tmp_path / "events.jsonl", "fixture")
    with pytest.raises(RuntimeError, match="fixture failure"):
        graph.run(state, "fixture", events, 2)
    record = summarize(events.path)
    assert record["outcome"] == "NO_TERMINAL_EVENT"
    [stage] = record["stages"]
    assert stage["status"] == "FAILED"
    assert stage["attempts"] == stage["timed_attempts"] == 1
    assert stage["duration_ms"] >= 0


def test_requirement_decision_survives_restart_and_rejects_stale_digest(tmp_path):
    database = tmp_path / "spec.db"
    draft = {"issue": "CP-101", "status": "AWAITING_APPROVAL", "acceptance_criteria": ["fixture"]}
    draft["spec_sha256"] = specification_digest(draft)
    run_id = SpecificationStore(database).create(draft)

    reopened = SpecificationStore(database)
    with pytest.raises(ValueError, match="digest"):
        reopened.decide(run_id, "stale", "APPROVED", "reviewer", None)
    assert reopened.get(run_id)["status"] == "AWAITING_APPROVAL"
    approved = reopened.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None)
    assert approved["status"] == "APPROVED"
    assert approved["draft"] == draft
    assert approved == reopened.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None)
    with pytest.raises(ValueError, match="not awaiting"):
        reopened.decide(run_id, draft["spec_sha256"], "REJECTED", "second-reviewer", "fixture")


def test_missing_acceptance_criteria_cannot_be_approved(tmp_path):
    store = SpecificationStore(tmp_path / "spec.db")
    draft = {"issue": "CP-101", "status": "BLOCKED", "acceptance_criteria": []}
    draft["spec_sha256"] = specification_digest(draft)
    run_id = store.create(draft)
    with pytest.raises(ValueError, match="not awaiting"):
        store.decide(run_id, draft["spec_sha256"], "APPROVED", "reviewer", None)


def test_guardrail_metrics_separate_phases_and_unknown_labels():
    result = guardrail_effectiveness([
        {"id": "A", "phase": "runtime", "violation": True, "decision": "BLOCKED"},
        {"id": "B", "phase": "runtime", "violation": True, "decision": "ALLOWED"},
        {"id": "C", "phase": "runtime", "violation": None, "decision": "ALLOWED"},
        {"id": "B", "phase": "post_execution", "violation": True, "decision": "DETECTED"},
    ])
    assert result["runtime"]["block_rate_pct"] == 50
    assert result["runtime"]["escape_rate_pct"] == 50
    assert result["runtime"]["false_block_rate_pct"] is None
    assert result["runtime"]["unlabelled"] == 1
    assert result["post_execution"]["detection_rate_pct"] == 100


def test_review_metrics_count_one_per_defect_and_exclude_derived_findings():
    result = independently_reviewed(["D1", "D2"], [
        {"id": "A", "origin": "ai", "adjudication": "confirmed", "defect_id": "D1"},
        {"id": "B", "origin": "ai", "adjudication": "confirmed", "defect_id": "D1"},
        {"id": "C", "origin": "ai", "adjudication": "confirmed", "defect_id": "D2", "derived_from": "tool-D2"},
        {"id": "D", "origin": "ai", "adjudication": "unverified"},
        {"id": "E", "origin": "ai", "adjudication": "incorrect"},
        {"id": "tool-D2", "origin": "tool", "adjudication": "confirmed", "defect_id": "D2"},
    ])
    assert result["ai"] == {"tp": 1, "fp": 1, "fn": 1, "derived": 1, "unverified": 1,
                            "precision": 0.5, "recall": 0.5, "f1": 0.5}
    assert result["tool"]["tp"] == 1
