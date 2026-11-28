# AI-SDLC Framework Guide

This guide explains how to set up and use the Capstone AI-SDLC framework. It is
written for both operators and people new to software development.

> For the Jira-driven PetClinic workflow, invoke `/requirement ISSUE-KEY` in
> Copilot Agent. The skill reads Jira, derives acceptance criteria, implements
> and tests the change in the separate PetClinic checkout, then pauses for
> human review. The enterprise CLI remains a separate workflow with its own
> digest-bound approval gates. Skill names are lowercase and hyphenated.

## 1. What the framework is for

The framework takes a work item, turns it into explicit acceptance criteria,
generates production code and tests, checks the result, reviews it, and records
evidence. The intended quality objective is **traceable implementation**:
every acceptance criterion should map to code and one or more tests, and every
failed check or review finding should be visible and routed to a correction
rather than hidden.

An agent's completion claim is not proof of quality. A passing test suite is
not proof that every requirement was tested. Use the traceability and review
evidence, and keep human approval and independent verification meaningful.

## 2. Current capabilities and important gaps

| Need | What exists in this repository | Boundary to keep in mind |
|---|---|---|
| Environment setup | `setup_env.sh` installs Python dependencies, runs an offline smoke test, then offers optional service configuration. Copilot skills are in `.github/skills/`. | Setup does not install VS Code or configure Jira unless selected. |
| Jira requirements | `/requirement ISSUE-KEY` reads one issue with `get_jira_issue` and derives observable acceptance criteria. | The Jira MCP tool must be available to Copilot. If it is unavailable or the issue is unclear, stop before editing. |
| Coding and tests | The requirement workflow changes the separate PetClinic checkout and adds focused tests. | Preserve existing edits, stay on the checked-out branch, and keep framework files in Capstone. |
| Testing | Relevant PetClinic tests run after implementation and before human review. | Report exact commands and pass/fail/blocked results; never present a blocked test as passing. |
| Security and pattern review | `/dev-review` provides read-only advisory findings when requested. | Findings are not ground truth; the human reviewer makes the final decision. |
| Human review | The requirement workflow presents the issue, criteria, diff, test evidence, branch, and risks after tests. | Human review does not authorize commits, pushes, Jira updates, merges, or deployments. |
| Enterprise CLI, CI, deployment | The separate enterprise CLI retains its digest-bound approval and delivery lifecycle. | Do not mix its stored run and approval receipts with the direct slash-command PetClinic workflow. |
| Enterprise measurements | Workflow receipts and reports support evidence-based scoring and comparisons. | Missing or unlabelled evidence remains unknown; generated scores alone do not prove product quality or productivity improvement. |

## 3. Set up the environment

Run setup from the repository root, using a terminal:

```bash
bash setup_env.sh
```

The script requires Python 3.11 or newer. It creates `.venv`, installs the
project and development dependencies, and runs an offline smoke test with
mocked Jira/LLM behavior. The normal setup then offers optional LLM/Jira/
Confluence/GitHub configuration. Select only services you intend to use.
`--smoke-only` skips interactive configuration:

```bash
bash setup_env.sh --smoke-only
```

Keep tokens in the private `.env` or environment variables; never commit them.
The wizard can configure the Atlassian site
`https://capstoneprojectgrp4.atlassian.net/`, but the URL alone does not grant
access. You need an authorized Atlassian account and API token.

After setup, open the repository in VS Code and start Copilot Agent chat. The
Jira-driven slash command uses the available Jira MCP tool and edits the
configured PetClinic checkout directly; it does not run the enterprise CLI
workflow engine.

## 4. A layman's view of the flow

Think of a requirement as a promise, a test as a check of that promise, and a
review as a second opinion:

1. **Provide a Jira issue key.** Invoke `/requirement ISSUE-KEY`; the skill
   reads only that issue through Jira MCP.
2. **Make the request testable.** The skill derives observable acceptance
   criteria. If key behavior is missing or ambiguous, it asks for clarification
   and pauses before edits.
3. **Implement and test.** The skill checks the independent PetClinic checkout,
   preserves its existing work and branch, adds a focused change and tests,
   then runs relevant project tests.
4. **Review and decide.** After tests, the skill presents the Jira summary,
   criteria, diff, exact test results, branch, and remaining risks. A human
   decides whether to approve, reject, or request changes.
5. **Deliver separately.** Commits, pushes, Jira updates, merges, and
   deployments are not part of this flow and require separate authorization.

## 5. Run the workflow with Copilot slash commands

Invoke `/requirement ISSUE-KEY` for the end-to-end Jira-to-PetClinic flow.
`/dev-code` and `/dev-testing` can also be invoked separately when acceptance
criteria and a change already exist. `/dev-review` is read-only advisory
review, and `/quality-check` presents the final human review packet.

The enterprise CLI (`agentic_sdlc run` and its staged commands) remains
available for its separate digest-bound approval lifecycle. Do not describe
that lifecycle as running behind the slash skills or use its approval receipts
for direct workspace edits.

## 6. Copilot slash commands and human checkpoints

The repository includes user-invoked skills in `.github/skills/`. Open the
repository in VS Code, start an Agent chat, and type `/` to find them. The
end-to-end `/requirement` skill pauses for ambiguity before editing and for
human review after tests. If the skills do not appear, verify that the Copilot
harness supports workspace skills and start a new chat.

| Command | Skill file | Agent responsibility | Required human decision before next stage |
|---|---|---|---|
| `/sdlc-orchestrator` | `sdlc-orchestrator/SKILL.md` | Coordinate the Jira-to-PetClinic sequence through tests and final review. | Clarify ambiguous requirements before editing; human reviews after testing. |
| `/requirement ISSUE-KEY` | `requirement/SKILL.md` | Read one Jira issue, derive criteria, implement and test in PetClinic. | Human reviews the complete change and evidence after tests. |
| `/dev-code` | `dev-code/SKILL.md` | Implement established acceptance criteria and add focused tests. | Preserve user changes; tests are run by `/dev-testing` or `/requirement`. |
| `/dev-testing` | `dev-testing/SKILL.md` | Run relevant project tests and report exact outcomes. | Report failures and blocked checks accurately. |
| `/quality-check` | `quality-check/SKILL.md` | Present a read-only final human review packet. | Human decides approve, reject, or request changes. |
| `/dev-review` | `dev-review/SKILL.md` | Provide read-only advisory findings. | Human makes the final decision. |

These slash skills operate directly on the workspace and are distinct from
the enterprise CLI's stored runs, scores, and approval receipts.

### Keeping coding and testing independent

The enterprise generation implementation already has useful separation:
code and test agents receive isolated copies of the same source snapshot,
the code agent is restricted to production files, the test agent is restricted
to test files, and their outputs are checked and integrated against a
manifest. This is independence of workspaces, not a guarantee of unbiased
tests: both agents still receive the same approved requirement and can share
model/prompt weaknesses.

To improve defect discovery, keep tester instructions focused on acceptance
criteria and observable behavior; don't give the tester coding-agent
conclusions as expected truth. Let the tester inspect the existing repository
and approved interface, but do not reveal hidden evaluation answers or tell
it to make tests pass. Preserve failing tests and report them. Measure tester
recall and false positives against independently adjudicated defects, not
against the coding agent's opinion or only against tests that happen to pass.

## 7. Metrics: what can and cannot be claimed

Collect at least these per-requirement values: approved acceptance criteria,
criteria with linked tests, criteria with test evidence, generated production
and test files, build/lint/test status, test/fix attempts, review findings by
severity and disposition, review retries, regression defects, human review
time, and final approval status.

Existing evidence is useful but has limits:

| Evidence/metric | Existing source | Interpretation |
|---|---|---|
| Stage events and durations | `events.jsonl`, summarized by `/sdlc-orchestrator`. | Workflow history/timing. It is not a code-completion percentage. |
| Criteria-to-code/test trace and score | `traceability.md`, including the number of criteria traced and listed code/test files. | Traceability signal; not proof that tests are sufficient or correct. |
| Review findings and quality score | `review-<REQ>.json`; main policy uses weighted severity penalties and consensus. | Finding/threshold summary; consensus is not independently labelled truth. |
| Review precision/recall/F1 | Quality reports based on independently adjudicated defect labels. | Meaningful only when labels are independently adjudicated and comparable. |
| Runtime guardrail blocks/escapes | Quality reports based on uniquely identified, independently labelled observations. | Separates blocked violations from legitimate actions that were falsely blocked. |
| Artifact coverage / stage scores | The workflow's evidence and quality report. | Missing inputs remain unavailable; a score is not proof of product quality. |

Do not report “percent of code completed” just from generated file count or
lines changed. Prefer criterion-level coverage with a named denominator:
`criteria with verified passing tests / total approved criteria`. Keep
unverified criteria visible. “Bugs caught” should mean independently confirmed
defects detected, with false alerts and missed defects reported alongside it.
The enterprise comparison tool requires equivalent task/specification and
acceptance identities; its output is not, on its own, causal evidence.
The workflow report summarizes production/test files and changed lines per
attempt, independent check pass/failure counts, review findings, and
independently adjudicated defect counts when available. These are size and
evidence metrics, not a percent-complete estimate; without independent
adjudication, confirmed bug counts remain unavailable.

## 8. Run artifacts and when they appear

The legacy run output directory is
`.agentic_sdlc/runs/<run-id>/` by default; `--out` changes the parent. These
are the conditions for the requested files:

| File | Meaning | Written when |
|---|---|---|
| `events.jsonl` | Append-only run events, stage outcomes, and requirement/run identifiers. | Created at run start; may contain a partial history if the run stops early. |
| `knowledge_base.json` | Requirements and human clarifications stored for the run. | Written when the workflow reaches the knowledge-base save step. |
| `review-<REQ>.json` | One requirement's review score, pass flag, iterations, voters, findings, consensus, trace, and reviewer metrics. | Written when that requirement reaches quality scoring; an earlier halt means the file may not exist. |
| `traceability.md` | Requirement status, code/test files, traced acceptance-criteria count, and score. | Written when the workflow reaches the DoD/documentation step, after regression succeeds. |
| `DoD.md` | Checklist derived from actual run state. | Written at the same documentation step; dry-run integration is explicitly left unchecked. |
| `release_notes.md` | Approved requirements and any undelivered requirements. | Written at the same documentation step. |
| `CONCERN.md` | Reason a run halted or needs human intervention. | Written only when a concern is raised; it is not expected on every successful run. |

The slash-command workflow records separate run artifacts, approval receipts,
manifests, digests, score reports, and timelines. Do not assume those files
are inside the legacy run directory or that they have the same approval
semantics.

## 9. Merge, regression, deployment, and release gates

In the existing runner, dry-run is the default. Git/Gerrit integration has a
human merge gate, and CI runs the configured `ci.command` or the repository's
detected build/lint/test commands. If regression fails, the workflow may
create a Jira defect (only when Jira defect settings are enabled), route
approved work back through correction/review, and retry up to
`loops.max_ci_iterations`; exhausted retries raise a concern and halt.

Deployment asks for approval per configured environment. Deployment is also
dry-run by default, and requires a configured command plus
`deploy.dry_run: false` to perform the action. Treat that switch as a real
operational authorization, not a demo convenience. Verify the final run status
and artifact evidence; reaching a generated `DoD.md` or release note does not
mean deployment occurred.

The approval-gated workflow applies its own rubric and mandatory gates.
Scoring does not replace merge or deployment approval. The
enterprise-implementation checklist is a maintainer reference for known
unfinished and deferred verification; read it before claiming an end-to-end
release gate is fully verified.

## 10. Project structure

```text
setup_env.sh                       shell entry point for environment setup
setup_env.py                       Python environment/configuration wizard
.github/skills/                    Copilot SDLC slash-command skills
src/agentic_sdlc/cli.py             `run`, `benchmark`, and `dev` command parsing
src/agentic_sdlc/orchestrator.py    legacy graph and enterprise workflow driver
src/agentic_sdlc/agents/            requirements, coding, testing, and review agents
src/agentic_sdlc/prompts/           role prompts and review/triage guidance
src/agentic_sdlc/jira.py            Jira reads and optional defect/write support
src/agentic_sdlc/development.py     isolated enterprise code/test generation
src/agentic_sdlc/quality.py         review scoring and metrics calculations
src/agentic_sdlc/verification.py    artifact-bound verification and approvals
src/agentic_sdlc/metrics.py         labelled review and guardrail metrics
src/agentic_sdlc/docs.py            traceability, DoD, and release-note generation
src/agentic_sdlc/sdlc.yaml          packaged defaults and guardrail/retry policy
docs/enterprise-implementation.md   enterprise workflow status, commands, limitations
examples/                           sample requirements, repos, and offline fixtures
tests/                              unit, integration, and workflow tests
```

## 11. Spring PetClinic example

Keep Capstone and Spring PetClinic as separate Git checkouts. The PetClinic
checkout is the development target. Capstone stores the reference snapshot
at `repos/spring-petclinic/`. Do not initialize a nested Git repository or use the benchmark
snapshot as the live target.

From the Capstone repository root, set `CAPSTONE_TARGET_REPO=../spring-petclinic`
in the local `.env` file. The approval-bound `workflow-start` uses this target
when `--repo` is omitted; an explicit `--repo` overrides it. Relative paths
are resolved from the directory where the framework command runs.

For example, with sibling checkouts:

```text
Capstone-workspace/
├── Capstone/          # SDLC framework Git repository
?   └── repos/
?       └── spring-petclinic/  # reference snapshot
└── spring-petclinic/  # target application Git repository
```

Run the framework from its repository root. Keep framework run artifacts in
the framework checkout; the default `.agentic_sdlc/runs/` location is relative
to the current directory. For the legacy CLI, specify both paths explicitly:

```bash
cd Capstone
python -m agentic_sdlc run --requirements PATH/TO/REQUIREMENTS.md \
  --repo ../spring-petclinic --out .agentic_sdlc/runs
```

For the Jira-driven slash-command workflow, invoke `/requirement ISSUE-KEY`.
It reads the issue with the Jira MCP tool, derives acceptance criteria,
implements the requested change in the configured PetClinic checkout, runs
relevant tests, then presents the result for human review. It pauses earlier
if the issue is ambiguous or the target has conflicting local edits. The
separate enterprise CLI workflow retains its own digest-bound stage approvals;
do not mix that lifecycle with the direct slash-command workflow.

Before generating code, inspect the target project's conventions and get
specific acceptance criteria from the product owner. For example, a request
could validate a new owner contact field; the ticket must specify permitted
formats, required/optional behavior, error response, and persistence
expectations.

Write one criterion per observable behavior, then map each to relevant test
scenarios such as valid input, invalid input, boundary values, and regression
behavior. `/requirement` performs this mapping, changes the configured
PetClinic checkout, and runs tests before human review. This is a usage pattern;
it does not assert a particular PetClinic feature or test result.

## 12. Further reading

- [Project overview and quick start](../README.md)
- [Enterprise implementation status for maintainers](./enterprise-implementation.md)
- [Implementation requirements matrix](./implementation-requirements-matrix.md)
