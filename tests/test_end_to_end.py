import json

from conftest import EXAMPLES, make_cfg, run_flow

from agentic_sdlc.cli import main
from agentic_sdlc.hitl import HITL
from agentic_sdlc.orchestrator import END, HALT, Orchestrator
from agentic_sdlc.state import Status

DIAGRAM_PATH = [
    "start", "jira_context", "gather", "knowledge_base", "planner", "repo_given?", "guardrails",
    "develop", "evaluate", "tests_passed?", "all_coded?", "security_review", "pattern_review",
    "ensemble_review", "quality_scoring", "quality_met?", "all_reviewed?", "gerrit", "cicd",
    "regression?", "dod", "deploy", END,
]


def test_happy_path_follows_diagram_and_writes_artifacts(tmp_path, repo, cfg):
    _, state = run_flow(tmp_path, repo, cfg)
    assert state.history == DIAGRAM_PATH
    assert state.work["REQ-001"].status == Status.APPROVED
    out = state.out_dir
    for name in ("events.jsonl", "review-REQ-001.json", "traceability.md", "DoD.md",
                 "release_notes.md", "knowledge_base.json"):
        assert (out / name).exists(), name
    review = json.loads((out / "review-REQ-001.json").read_text())
    assert review["passed"] and review["score"] == 1.0
    assert [d["env"] for d in state.deployments] == ["dev", "qa", "staging", "prod"]
    assert "[ ] Change integrated into Git / Gerrit (dry-run" in (out / "DoD.md").read_text()


def test_iterates_over_every_requirement(tmp_path, repo, cfg):
    _, state = run_flow(tmp_path, repo, cfg, count=2)
    assert state.history.count("develop") == 2
    assert state.history.count("security_review") == 2
    assert all(w.status == Status.APPROVED for w in state.work.values())


def test_missing_repo_raises_concern_and_halts(tmp_path, cfg):
    reqs = tmp_path / "r.md"
    reqs.write_text("## REQ-001: x\n", encoding="utf-8")
    state = Orchestrator(cfg).run([reqs], None, tmp_path / "runs")
    assert state.history[-3:] == ["repo_given?", "concern", HALT]
    assert (state.out_dir / "CONCERN.md").exists()


def test_human_provides_repo_path_loops_back_to_gather(tmp_path, repo):
    cfg = make_cfg(hitl={"mode": "interactive"})
    hitl = HITL(cfg, input_fn=lambda prompt: str(repo) if "repository path" in prompt else "y")
    reqs = tmp_path / "r.md"
    reqs.write_text("## REQ-001: x\nAcceptance criteria:\n- x works\n", encoding="utf-8")
    state = Orchestrator(cfg, hitl=hitl).run([reqs], None, tmp_path / "runs")
    assert state.history.count("gather") == 2
    assert state.history[-1] == END


def test_requirements_not_signed_off_halts(tmp_path, repo):
    cfg = make_cfg(hitl={"auto_approve": []})
    _, state = run_flow(tmp_path, repo, cfg)
    assert state.history[-1] == HALT
    assert "signed off" in state.concern


def test_jira_issues_are_merged_into_requirements(tmp_path, repo):
    cfg = make_cfg(jira={"issues_file": str(EXAMPLES / "jira_issues.json")})
    _, state = run_flow(tmp_path, repo, cfg)
    assert set(state.work) == {"REQ-001", "CAP-101"}
    assert state.work["CAP-101"].requirement.acceptance_criteria == [
        "divide returns the quotient of two numbers", "divide by zero raises an error"]


def test_cli_run_returns_zero(tmp_path, repo, capsys):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text("repo: {build_command: none, lint_command: none}\n"
                        "review: {external_tools: none}\n", encoding="utf-8")
    code = main(["run", "--requirements", str(EXAMPLES / "requirements.md"), "--repo", str(repo),
                 "--config", str(cfg_file), "--out", str(tmp_path / "runs")])
    assert code == 0
    assert "finished at 'end'" in capsys.readouterr().out
