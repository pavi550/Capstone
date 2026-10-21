"""3. Orchestrator agent (AI PM) and the state machine for the whole Agentic SDLC flow.

The graph mirrors `docs/presentation/Agentic_SDLC_Flowdiagram1.html`: every box is a node,
every diamond is a decision node whose router picks the next edge, and every dashed arrow is
a feedback loop bounded by `loops.max_*_iterations`. The `Graph` API (nodes + conditional
edges) follows LangGraph so a LangGraph backend can be swapped in without changing nodes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import cicd, deploy, docs, jira
from .agents.coding import CodingAgent
from .agents.review import ReviewAgent
from .agents.testing import TestAgent
from .config import Config
from .ensemble import Ensemble
from .events import EventLog
from .guardrails import Guardrails
from .hitl import HITL
from .knowledge_base import KnowledgeBase, make_store, parse_requirements
from .llm import LLM, load_prompt, make_llm, parse_json
from .metrics import quality_score, reviewer_metrics
from .quality import run_checks, triage
from .repo_adapter import commands_for
from .state import CheckResult, Requirement, RunState, Status, Work
from .vcs import VCS

END, HALT = "end", "halt"
Router = Callable[[RunState], str]


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Callable[[RunState], None]] = {}
        self.edges: dict[str, str | Router] = {}

    def add_node(self, name: str, fn: Callable[[RunState], None]) -> None:
        self.nodes[name] = fn

    def add_edge(self, source: str, target: str | Router) -> None:
        self.edges[source] = target

    def run(self, state: RunState, start: str, events: EventLog, max_steps: int) -> str:
        node = start
        while node not in (END, HALT):
            if len(state.history) >= max_steps:
                state.concern = f"state machine exceeded loops.max_steps={max_steps}"
                node = HALT
                break
            state.history.append(node)
            events.emit(node, "enter", requirement=state.current, phase=state.phase)
            self.nodes[node](state)
            route = self.edges[node]
            node = route(state) if callable(route) else route
        state.history.append(node)
        events.emit(node, "finish", concern=state.concern)
        return node


class Orchestrator:
    def __init__(self, cfg: Config, llm: LLM | None = None, hitl: HITL | None = None,
                 vcs: VCS | None = None):
        self.cfg = cfg
        self.llm = llm or make_llm(cfg)
        self.hitl = hitl or HITL(cfg)
        self.guardrails = Guardrails(cfg)
        self.ensemble = Ensemble(cfg)
        self._vcs = vcs
        self.commands: dict = {}

    # ------------------------------------------------------------------ public API

    def run(self, requirement_sources: list[Path], repo: Path | None,
            out_dir: Path | None = None) -> RunState:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        out = Path(out_dir or self.cfg.get("project.output_dir")) / run_id
        out.mkdir(parents=True, exist_ok=True)
        self.state = RunState(run_id=run_id, out_dir=out, repo_path=repo,
                              requirement_sources=[Path(p) for p in requirement_sources])
        self.events = EventLog(out / "events.jsonl", run_id)
        self.kb = KnowledgeBase(make_store(self.cfg), out / self.cfg.get("knowledge_base.path"))
        top_k = self.cfg.get("knowledge_base.top_k", 3)
        self.coder = CodingAgent(self.llm, self.guardrails, self.kb, top_k)
        self.tester = TestAgent(self.llm, self.guardrails, self.kb, top_k)
        self.reviewer = ReviewAgent(self.llm, self.guardrails, self.cfg)
        self.vcs = self._vcs or VCS(self.cfg, self.events, self.hitl, self.guardrails)
        self.events.emit("start", "run", llm=self.llm.name, repo=str(repo) if repo else None)
        self.build_graph().run(self.state, "start", self.events, self.cfg.get("loops.max_steps"))
        self.state.outputs["events"] = str(self.events.path)
        return self.state

    def build_graph(self) -> Graph:
        g = Graph()
        nodes = {
            "start": lambda s: None,
            "jira_context": self.jira_context,      # Jira dashboard -> RAG -> vector DB
            "gather": self.gather,                  # 1
            "knowledge_base": self.knowledge_base,  # 2
            "planner": self.planner,                # 3
            "repo_given?": lambda s: None,
            "concern": self.concern,
            "guardrails": self.guardrails_check,    # 4
            "develop": self.develop,                # 5a + 5b in parallel
            "coder": self.code,                     # 5a (refinement)
            "tester": self.test,                    # 5b (test fix)
            "evaluate": self.evaluate,              # 6
            "tests_passed?": lambda s: None,
            "triage": self.triage,
            "fix_test": self.fix_test,
            "fix_code": self.fix_code,
            "fix_requirement": self.fix_requirement,
            "all_coded?": self.next_build,
            "security_review": self.security_review,    # 7a
            "pattern_review": self.pattern_review,      # 7b
            "ensemble_review": self.ensemble_review,    # 7c
            "quality_scoring": self.quality_scoring,    # 8
            "quality_met?": lambda s: None,
            "review_fix": self.review_fix,
            "all_reviewed?": self.next_review,
            "gerrit": self.integrate,               # 9
            "cicd": self.cicd,                      # 10
            "regression?": lambda s: None,
            "defect": self.defect,
            "dod": self.dod,                        # 11
            "deploy": self.deploy,                  # 12
        }
        for name, fn in nodes.items():
            g.add_node(name, fn)
        g.add_edge("start", "jira_context")
        g.add_edge("jira_context", "gather")
        g.add_edge("gather", lambda s: HALT if s.concern else "knowledge_base")
        g.add_edge("knowledge_base", "planner")
        g.add_edge("planner", "repo_given?")
        g.add_edge("repo_given?", lambda s: "guardrails" if s.repo_path else "concern")
        g.add_edge("concern", lambda s: "gather" if s.repo_path else HALT)
        g.add_edge("guardrails", lambda s: "concern" if s.concern else "develop")
        g.add_edge("develop", "evaluate")
        g.add_edge("coder", lambda s: "evaluate" if s.cur.test_files else "tester")
        g.add_edge("tester", "evaluate")
        g.add_edge("evaluate", "tests_passed?")
        g.add_edge("tests_passed?", self._route_tests)
        g.add_edge("triage", self._route_triage)
        g.add_edge("fix_test", "tester")
        g.add_edge("fix_code", "coder")
        g.add_edge("fix_requirement", "coder")
        g.add_edge("all_coded?", self._route_all_coded)
        g.add_edge("security_review", "pattern_review")
        g.add_edge("pattern_review", "ensemble_review")
        g.add_edge("ensemble_review", "quality_scoring")
        g.add_edge("quality_scoring", "quality_met?")
        g.add_edge("quality_met?", lambda s: "all_reviewed?" if s.cur.status == Status.APPROVED
                   else "review_fix")
        g.add_edge("review_fix", lambda s: "coder" if s.cur.status != Status.REVIEW_FAILED
                   else "all_reviewed?")
        g.add_edge("all_reviewed?", lambda s: "security_review" if s.current else "gerrit")
        g.add_edge("gerrit", lambda s: HALT if s.concern else "cicd")
        g.add_edge("cicd", "regression?")
        g.add_edge("regression?", lambda s: "dod" if s.ci_result.passed else "defect")
        g.add_edge("defect", lambda s: HALT if s.concern else "coder")
        g.add_edge("dod", "deploy")
        g.add_edge("deploy", END)
        return g

    # ------------------------------------------------------------------ helpers

    def _raise_concern(self, message: str) -> None:
        self.state.concern = message
        path = self.state.out_dir / "CONCERN.md"
        path.write_text(f"# Concern raised\n\n{message}\n\nRun: `{self.state.run_id}`\n",
                        encoding="utf-8")
        self.state.outputs["concern"] = str(path)
        self.events.emit("concern", "raised", message=message)

    def _feedback(self, work: Work, kind: str, text: str) -> None:
        work.feedback, work.feedback_kind = text, kind

    # ------------------------------------------------------------------ Part 1 nodes

    def jira_context(self, s: RunState) -> None:
        reqs = jira.fetch_requirements(self.cfg)
        for r in reqs:
            self.kb.upsert(r)
        s.requirements.extend(reqs)
        self.events.emit("jira_context", "fetched", count=len(reqs))

    def gather(self, s: RunState) -> None:
        s.concern = None
        merged = {r.id: r for r in s.requirements}
        for src in s.requirement_sources:
            for r in parse_requirements(src):
                merged[r.id] = r
        s.requirements = list(merged.values())
        self.events.emit("gather", "requirements", ids=[r.id for r in s.requirements])
        if not s.requirements:
            self._raise_concern("no requirements found in the given sources or Jira")
        elif not self.hitl.approve("requirements_signoff", f"{len(s.requirements)} requirements"):
            self._raise_concern("requirements were not signed off by a human")

    def knowledge_base(self, s: RunState) -> None:
        for r in s.requirements:
            self.kb.upsert(r)
        s.outputs["knowledge_base"] = str(self.kb.save())

    def planner(self, s: RunState) -> None:
        user = json.dumps({"requirements": [r.text() for r in s.requirements]})
        reply = parse_json(self.llm.complete(
            "plan", load_prompt("TaskPlanner.md"), user,
            {"requirements": [r.to_dict() for r in s.requirements]},
        ))
        s.plan = reply
        s.work = {r.id: Work(requirement=r) for r in s.requirements}
        s.build_queue = [r.id for r in s.requirements]
        self.events.emit("planner", "plan", tasks=len(reply.get("tasks", [])))

    def concern(self, s: RunState) -> None:
        if not s.concern:
            self._raise_concern("repository path not given / not available")
        answer = self.hitl.ask(f"{s.concern}. Provide a repository path (blank to stop):")
        s.repo_path = Path(answer) if answer else None
        if s.repo_path:
            self.events.emit("concern", "resolved", repo=answer)

    def guardrails_check(self, s: RunState) -> None:
        violations = self.guardrails.check_repo(s.repo_path)
        if self.cfg.get("vcs.mode") == "gerrit":
            violations += self.guardrails.check_mcp("gerrit")
        if violations:
            s.repo_path = None
            self._raise_concern("; ".join(str(v) for v in violations))
            return
        self.commands = commands_for(s.repo_path, self.cfg)
        self.events.emit("guardrails", "passed", commands=self.commands)
        s.current = s.build_queue.pop(0)

    def develop(self, s: RunState) -> None:
        work, repo = s.cur, s.repo_path
        with ThreadPoolExecutor(max_workers=2) as pool:
            code = pool.submit(self.coder.implement, work, repo)
            test = pool.submit(self.tester.write_tests, work, repo)
            _, code_v = code.result()
            _, test_v = test.result()
        work.pending_violations = [str(v) for v in code_v + test_v]
        self.events.emit("develop", "generated", files=work.files, tests=work.test_files,
                         violations=work.pending_violations)

    def code(self, s: RunState) -> None:
        work = s.cur
        task = "fix_review" if work.feedback_kind == "review" else "code"
        _, violations = self.coder.implement(work, s.repo_path, work.feedback, task)
        work.pending_violations += [str(v) for v in violations]
        self.events.emit("coder", "refined", kind=work.feedback_kind, files=work.files,
                         violations=work.pending_violations)
        work.feedback = work.feedback_kind = None

    def test(self, s: RunState) -> None:
        work = s.cur
        _, violations = self.tester.write_tests(work, s.repo_path, work.feedback)
        work.pending_violations += [str(v) for v in violations]
        self.events.emit("tester", "tests", tests=work.test_files, violations=violations)
        work.feedback = work.feedback_kind = None

    def evaluate(self, s: RunState) -> None:
        work = s.cur
        violations, work.pending_violations = work.pending_violations, []
        if violations:
            work.last_check = CheckResult(
                passed=False, output="Guardrail violations:\n" + "\n".join(violations))
        else:
            work.last_check = run_checks(s.repo_path, self.commands,
                                         self.cfg.get("quality.min_coverage"))
        self.events.emit("evaluate", "checked", passed=work.last_check.passed,
                         steps=work.last_check.steps, coverage=work.last_check.coverage)

    def _route_tests(self, s: RunState) -> str:
        if not s.cur.last_check.passed:
            return "triage"
        if s.phase == "build":
            s.cur.status = Status.TESTED
            return "all_coded?"
        return "security_review"

    def triage(self, s: RunState) -> None:
        work = s.cur
        if work.fix_iterations >= self.cfg.get("loops.max_fix_iterations"):
            work.status = Status.BLOCKED
            self.events.emit("triage", "blocked", reason="loops.max_fix_iterations reached")
            return
        work.fix_iterations += 1
        category, reason = triage(self.llm, work, work.last_check)
        work.triage_history.append(category)
        self._feedback(work, category, f"{reason}\n\n{work.last_check.output[-4000:]}")
        self.events.emit("triage", "classified", category=category, reason=reason,
                         iteration=work.fix_iterations)

    def _route_triage(self, s: RunState) -> str:
        if s.cur.status == Status.BLOCKED:
            return "all_coded?" if s.phase == "build" else "all_reviewed?"
        return {"test": "fix_test", "code": "fix_code",
                "requirement": "fix_requirement"}[s.cur.feedback_kind]

    def fix_test(self, s: RunState) -> None:
        self.events.emit("fix_test", "update_tests")

    def fix_code(self, s: RunState) -> None:
        self.events.emit("fix_code", "refine_code")

    def fix_requirement(self, s: RunState) -> None:
        work = s.cur
        req: Requirement = self.hitl.requirement_update(work.requirement, work.feedback or "")
        self.kb.upsert(req)
        self.kb.save()
        self.events.emit("fix_requirement", "kb_updated", notes=req.notes)

    def next_build(self, s: RunState) -> None:
        if s.build_queue:
            s.current = s.build_queue.pop(0)
            return
        s.phase = "review"
        s.review_queue = [w.requirement.id for w in s.by_status(Status.TESTED)]
        s.current = s.review_queue.pop(0) if s.review_queue else None

    def _route_all_coded(self, s: RunState) -> str:
        if s.phase == "build":
            return "develop"
        return "security_review" if s.current else "all_reviewed?"

    # ------------------------------------------------------------------ Part 2 nodes

    def security_review(self, s: RunState) -> None:
        s.cur.findings["security"] = self.reviewer.security(s.repo_path, s.cur)

    def pattern_review(self, s: RunState) -> None:
        s.cur.findings["pattern"], s.cur.trace = self.reviewer.pattern(s.repo_path, s.cur)

    def ensemble_review(self, s: RunState) -> None:
        work = s.cur
        work.findings["llm"] = self.reviewer.llm_vote(s.repo_path, work)
        voters = {"rules": work.findings["security"] + work.findings["pattern"],
                  "llm": work.findings["llm"]}
        groups = self.ensemble.vote(voters)
        work.groups = [g.to_dict() for g in groups]
        work.consensus = [g.representative for g in groups if g.kept]
        work.reviewer_metrics = reviewer_metrics(work.groups, list(voters))

    def quality_scoring(self, s: RunState) -> None:
        work = s.cur
        work.score = quality_score(work.consensus)
        blocking = [f for f in work.consensus
                    if f.severity in self.cfg.get("quality.block_severities", [])]
        passed = work.score >= self.cfg.get("quality.review_threshold") and not blocking
        if passed:
            work.status = Status.APPROVED
        review = {
            "requirement": work.requirement.id, "score": work.score, "passed": passed,
            "iteration": work.review_iterations, "voters": ["rules", "llm"],
            "findings": {k: [f.to_dict() for f in v] for k, v in work.findings.items()},
            "consensus": [f.to_dict() for f in work.consensus], "groups": work.groups,
            "trace": work.trace, "reviewer_metrics": work.reviewer_metrics,
        }
        path = s.out_dir / f"review-{work.requirement.id}.json"
        path.write_text(json.dumps(review, indent=2), encoding="utf-8")
        self.events.emit("quality_scoring", "scored", score=work.score, passed=passed,
                         consensus=len(work.consensus))

    def review_fix(self, s: RunState) -> None:
        work = s.cur
        if work.review_iterations >= self.cfg.get("loops.max_review_iterations"):
            work.status = Status.REVIEW_FAILED
            self.events.emit("review_fix", "review_failed",
                             reason="loops.max_review_iterations reached")
            return
        work.review_iterations += 1
        text = "\n".join(f"[{f.severity}] {f.file}:{f.line} {f.message}" for f in work.consensus)
        self._feedback(work, "review", f"Fix these review findings:\n{text}")
        self.events.emit("review_fix", "requested", iteration=work.review_iterations)

    def next_review(self, s: RunState) -> None:
        s.current = s.review_queue.pop(0) if s.review_queue else None

    def integrate(self, s: RunState) -> None:
        approved = s.by_status(Status.APPROVED)
        s.vcs_result = self.vcs.integrate(s.repo_path, approved, s.run_id)
        self.events.emit("gerrit", "integrated", result=s.vcs_result)
        if not s.vcs_result.get("ok"):
            self._raise_concern(f"Git/Gerrit integration failed: {s.vcs_result.get('error')}")

    def cicd(self, s: RunState) -> None:
        s.ci_result = cicd.run_pipeline(s.repo_path, self.cfg, self.commands)
        self.events.emit("cicd", "pipeline", passed=s.ci_result.passed, steps=s.ci_result.steps)

    def defect(self, s: RunState) -> None:
        approved = s.by_status(Status.APPROVED)
        if s.ci_iterations >= self.cfg.get("loops.max_ci_iterations") or not approved:
            self._raise_concern("CI regression failures persist (loops.max_ci_iterations reached)"
                                if approved else
                                "CI regression fails and no approved change is available to refine")
            return
        s.ci_iterations += 1
        defect_key = self._raise_jira_defect(s)
        for w in approved:
            w.status, w.review_iterations = Status.TESTED, 0
        s.phase = "review"
        s.review_queue = [w.requirement.id for w in approved]
        s.current = s.review_queue.pop(0)
        self._feedback(s.cur, "regression",
                       f"CI regression failure (defect {defect_key or 'n/a'}):\n"
                       f"{s.ci_result.output[-4000:]}")
        self.events.emit("defect", "raised", jira=defect_key, iteration=s.ci_iterations)

    def _raise_jira_defect(self, s: RunState) -> str | None:
        project = self.cfg.get("jira.defect_project")
        if not (self.cfg.get("jira.enabled") and project):
            return None
        try:
            return jira.JiraClient(self.cfg).create_issue(
                project, f"Regression in agentic run {s.run_id}",
                s.ci_result.output[-3000:], self.cfg.get("jira.defect_issuetype", "Bug"))
        except (RuntimeError, OSError, KeyError) as exc:  # optional; never block the loop
            self.events.emit("defect", "jira_error", error=str(exc))
            return None

    def dod(self, s: RunState) -> None:
        s.outputs.update(docs.write_docs(s, s.out_dir))

    def deploy(self, s: RunState) -> None:
        s.deployments = deploy.deploy(self.cfg, self.hitl, s.repo_path)
        self.events.emit("deploy", "promoted", environments=s.deployments)
