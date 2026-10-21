import sys

from conftest import ScriptedLLM, code_reply, make_cfg, run_flow

from agentic_sdlc.orchestrator import END, HALT
from agentic_sdlc.state import Status


def broken_code(ctx):
    return code_reply(ctx["requirement"], describe="wrong")


def test_code_incorrect_loops_through_refinement(tmp_path, repo, cfg):
    llm = ScriptedLLM(code=[broken_code])
    _, state = run_flow(tmp_path, repo, cfg, llm)
    work = state.work["REQ-001"]
    assert work.triage_history == ["code"] and work.fix_iterations == 1
    assert "fix_code" in state.history and work.status == Status.APPROVED


def test_test_incorrect_loops_through_test_update(tmp_path, repo, cfg):
    bad_test = {"files": [{"path": "tests/test_req_001.py",
                           "content": "def test_bad():\n    assert 1 == 2\n"}]}
    llm = ScriptedLLM(test=[bad_test], triage=[{"category": "test", "reason": "bad expectation"}])
    _, state = run_flow(tmp_path, repo, cfg, llm)
    assert "fix_test" in state.history
    assert state.history.index("tester") > state.history.index("fix_test")
    assert state.work["REQ-001"].status == Status.APPROVED


def test_requirement_incorrect_updates_knowledge_base(tmp_path, repo, cfg):
    llm = ScriptedLLM(code=[broken_code],
                      triage=[{"category": "requirement", "reason": "ambiguous"}])
    _, state = run_flow(tmp_path, repo, cfg, llm)
    work = state.work["REQ-001"]
    assert "fix_requirement" in state.history
    assert work.requirement.notes and "ambiguous" in work.requirement.notes[0]
    assert "ambiguous" in (state.out_dir / "knowledge_base.json").read_text()
    assert work.status == Status.APPROVED


def test_fix_loop_is_bounded(tmp_path, repo):
    cfg = make_cfg(loops={"max_fix_iterations": 2})
    llm = ScriptedLLM(code=[broken_code] * 10)
    _, state = run_flow(tmp_path, repo, cfg, llm)
    work = state.work["REQ-001"]
    assert work.status == Status.BLOCKED and work.fix_iterations == 2
    assert state.history.count("triage") == 3
    # the blocked change is never approved, so CI on the working tree fails and a concern is raised
    assert state.history[-1] == HALT and "no approved change" in state.concern


def test_guardrail_violation_fails_evaluation_and_is_refined(tmp_path, repo, cfg):
    def sneaky(ctx):
        reply = code_reply(ctx["requirement"])
        reply["files"].append({"path": ".github/workflows/x.yml", "content": "on: push"})
        return reply

    llm = ScriptedLLM(code=[sneaky])
    _, state = run_flow(tmp_path, repo, cfg, llm)
    assert not (repo / ".github").exists()
    assert state.work["REQ-001"].triage_history == ["code"]
    assert state.work["REQ-001"].status == Status.APPROVED


def insecure_code(ctx):
    return code_reply(ctx["requirement"], extra="RESULT = eval('1 + 1')\n")


def test_review_findings_loop_back_through_code_refinement(tmp_path, repo, cfg):
    llm = ScriptedLLM(code=[insecure_code])
    _, state = run_flow(tmp_path, repo, cfg, llm)
    work = state.work["REQ-001"]
    assert "review_fix" in state.history and work.review_iterations == 1
    assert llm.calls.count("fix_review") == 1
    assert state.history.count("security_review") == 2
    assert work.status == Status.APPROVED


def test_review_loop_is_bounded(tmp_path, repo):
    cfg = make_cfg(loops={"max_review_iterations": 1})
    llm = ScriptedLLM(code=[insecure_code], fix_review=[insecure_code] * 5)
    _, state = run_flow(tmp_path, repo, cfg, llm)
    work = state.work["REQ-001"]
    assert work.status == Status.REVIEW_FAILED and work.review_iterations == 1
    assert state.vcs_result.get("note")


CI_FAILS_ONCE = [sys.executable, "-c",
                 ("import pathlib,sys; p=pathlib.Path('ci_marker'); ok=p.exists(); p.touch(); "
                  "sys.exit(0 if ok else 1)")]


def test_regression_failure_raises_defect_and_rereviews(tmp_path, repo):
    cfg = make_cfg(ci={"command": CI_FAILS_ONCE})
    _, state = run_flow(tmp_path, repo, cfg)
    assert state.ci_iterations == 1
    after = state.history[state.history.index("defect"):]
    assert after[:4] == ["defect", "coder", "evaluate", "tests_passed?"]
    assert "security_review" in after and state.history[-1] == END


def test_regression_loop_is_bounded(tmp_path, repo):
    cfg = make_cfg(ci={"command": [sys.executable, "-c", "raise SystemExit(1)"]},
                   loops={"max_ci_iterations": 1})
    _, state = run_flow(tmp_path, repo, cfg)
    assert state.history[-1] == HALT and state.ci_iterations == 1
    assert "max_ci_iterations" in state.concern
    assert (state.out_dir / "CONCERN.md").exists()


def test_max_steps_stops_runaway_graph(tmp_path, repo):
    cfg = make_cfg(loops={"max_steps": 5})
    _, state = run_flow(tmp_path, repo, cfg)
    assert state.history[-1] == HALT and "max_steps" in state.concern
