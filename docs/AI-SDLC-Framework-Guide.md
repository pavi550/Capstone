# AI-SDLC Framework Guide

This guide explains how to set up and use the Capstone AI-SDLC framework. It is
written for both operators and people new to software development.

> **Use one interface:** run the SDLC from Copilot Agent in VS Code using the
> slash commands listed in this guide. Start with `/sdlc-orchestrator`, or
> invoke an individual stage such as `/requirement` or `/dev-code`. The
> framework runs its command-line engine behind the scenes; users should not
> run a second set of `agentic_sdlc dev` commands. Skill names are lowercase
> and hyphenated, so `/devCode`, `/devtesting`, `/qualityCheck`, and
> `/devreview` are `/dev-code`, `/dev-testing`, `/quality-check`, and
> `/dev-review`.

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
| Jira requirements | `/requirement` reads the configured Jira issue and prepares a digest-bound requirement draft. | This guide does not verify the live Jira account, project permissions, or current backlog. A ticket needs acceptance criteria; provide missing criteria in the Copilot conversation. |
| Coding and tests | The slash-command workflow creates isolated code and test drafts, integrates them into a manifest, and pauses for human approval before testing. | Code/test generation is independent but concurrent; inspect and approve the combined artifact before execution. Test files follow the repository's existing framework; the agent does not install dependencies or rewrite protected framework configuration. |
| Testing | `/dev-testing` runs independent build, lint, and test checks and records artifact-bound outcomes. | Verification evidence must come from an independent trusted verifier; agent generation itself does not execute the generated code. |
| Security and pattern review | `/dev-review` gathers security, pattern, and configured model findings for human review. | Review results are not ground truth. Independent adjudication is required to measure missed bugs and false findings reliably. |
| Human approvals | The slash-command workflow pauses for digest-bound requirement, development, quality, and final review decisions. | Approvals are recorded by the internal engine using the configured local policy. A conversational response is not a replacement for required setup or identity separation. |
| Merge, CI, deployment | The workflow records merge, CI/regression, and per-environment deployment gates for human authorization. | Defaults are intentionally conservative: VCS and deployment are dry-run. A dry-run is not a merge or deployment. |
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

After setup, open the repository in VS Code, start Copilot Agent chat, and use
the slash commands below. The command-line engine and its configuration are
used internally by those skills; you do not need to launch a separate SDLC
command in a terminal.

## 4. A layman's view of the flow

Think of a requirement as a promise, a test as a check of that promise, and a
review as a second opinion:

1. **Choose the work item.** Find the Jira ticket or write down the requested
   change.
2. **Make it testable.** Each acceptance criterion should describe an outcome
   that a person can check. Resolve ambiguity with the product owner before
   asking an agent to code.
3. **Approve the specification.** Confirm the ticket, scope, and criteria are
   correct. A wrong or incomplete requirement produces misleading code and
   tests.
4. **Generate code and tests separately.** The coding agent owns production
   files; the testing agent owns test files. Neither should be told to conceal
   defects or to make a review pass.
5. **Inspect the proposed changes.** A human reads the requirement, code diff,
   tests, and any guardrail warnings before execution or approval.
6. **Run checks in order.** Execute the relevant tests and repository
   regression checks. Record pass/fail results against each requirement and
   fix failures with bounded retries.
7. **Review and decide.** Combine automated security/pattern findings with
   independent human review. Fix blockers and re-review; a human decides
   whether the exact reviewed changes are accepted.
8. **Merge and deploy only after approval.** Run CI/regression checks, route
   failures to defect handling, and request a separate deployment approval.
   Dry-run output is a plan, not a completed release.
9. **Keep the evidence.** Link criteria to code and tests, record metrics and
   decisions, and publish Definition of Done and release notes only when the
   lifecycle reaches the appropriate stage.

## 5. Run the workflow with Copilot slash commands

The slash commands are the single user-facing workflow. They guide Copilot
through Jira intake, code and test generation, independent verification,
quality review, and final review. The underlying CLI, saved workflow state,
artifact digests, and approval receipts are implementation details run by the
skills, not a second set of commands for the user.

Start with `/sdlc-orchestrator` for guided end-to-end use, or invoke the
individual skill for the stage you want:

1. `/requirement CP-101` reads the configured Jira issue, identifies testable
   acceptance criteria, and pauses for your decision. If criteria are missing
   or ambiguous, provide the product decision in chat before approving.
2. `/dev-code RUN_ID` creates isolated production-code and test drafts from the
   approved requirement, shows the combined artifact digest, and pauses for
   your inspection and approval.
3. `/dev-testing RUN_ID` runs independent checks only after development
   approval. It reports results and pauses rather than deciding whether the
   quality evidence is acceptable.
4. `/quality-check RUN_ID` presents the evidence and asks you to approve or
   reject the exact quality result.
5. `/dev-review RUN_ID` runs security, pattern, and configured model review,
   presents findings, and pauses for your decision.

If a stage fails or is rejected, the workflow stops. Requesting a correction
must be explicit; one correction cycle is bounded and returns the changed
artifact to the required approval and verification gates. Merge, CI writes,
and deployment require separate authorization.

The regular `run` path remains in the repository for compatibility and
maintainer testing. It is not an alternate user workflow for the approval-
gated SDLC described here.

## 6. Copilot slash commands and human checkpoints

The repository includes user-invoked skills in `.github/skills/`. Open the
repository in VS Code, start an Agent chat, and type `/` to find them. Each
skill is explicitly user-invoked, asks for missing inputs, displays its
output, and stops for a human decision before advancing. If the skills do not
appear, verify that the Copilot harness supports workspace skills and start a
new chat. A rejection or cancellation must stop the workflow without
pretending the stage succeeded.

| Command | Skill file | Agent responsibility | Required human decision before next stage |
|---|---|---|---|
| `/sdlc-orchestrator` | `sdlc-orchestrator/SKILL.md` | Coordinate the operator through the SDLC without auto-approving or silently advancing. | Explicit decision at every stage and before any external write. |
| `/requirement CP-101` | `requirement/SKILL.md` | Read Jira and draft a digest-bound requirement. | Confirm scope and criteria; persist approval/rejection against the exact specification digest. |
| `/dev-code RUN_ID` | `dev-code/SKILL.md` | Start integrated code/test generation for the approved specification. | Inspect and approve/reject the exact combined manifest digest. |
| `/dev-testing RUN_ID` | `dev-testing/SKILL.md` | Resume the approved run and execute independent verification. | Inspect recorded checks and evidence; then use `/quality-check` for the decision. |
| `/quality-check RUN_ID` | `quality-check/SKILL.md` | Review recorded quality results and the first-pass score. | Approve/reject the exact verification digest. |
| `/dev-review RUN_ID` | `dev-review/SKILL.md` | Run security, pattern, and configured model review after quality approval. | Inspect findings and approve/reject the exact review digest. |

All stages are independently triggerable, but they continue the same approved
requirement and workflow run. The coordinator skill directs the process; it
does not programmatically dispatch other skills. A later agent must not
silently replace an earlier accepted artifact. The human can request a
bounded correction or stop at every stage. No agent may auto-approve its own
work.

The workflow's first-pass score records check outcomes before quality
approval; the final score includes later approval and validation gates. Do not
treat either score as proof that all requirement criteria are satisfied
without examining the underlying checks and traceability evidence.

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
benchmarks/spring-petclinic/        Spring PetClinic reference project
tests/                              unit, integration, and workflow tests
```

## 11. Spring PetClinic example

Use PetClinic as a **target application example**, not as a claim that the
repository already contains a PetClinic Jira ticket or that the live Jira
project is connected. First inspect the target project's conventions and use
a disposable copy. Ask the product owner for exact behaviors; for instance,
a request could be to validate a new owner contact field. The actual ticket
must specify permitted formats, required/optional behavior, error response,
and persistence expectations before code is generated.

Write one criterion per observable behavior, then map each to test scenarios:
valid input, invalid input, boundary values, and regression behavior. Keep the
acceptance tests in a trusted directory separate from generated output. Use
`/requirement`, `/dev-code`, `/dev-testing`, `/quality-check`, and
`/dev-review` to advance through the approval gates. This is a usage pattern;
it does not assert a particular PetClinic feature or test result.

## 12. Further reading

- [Project overview and quick start](../README.md)
- [Enterprise implementation status for maintainers](./enterprise-implementation.md)
- [Implementation requirements matrix](./implementation-requirements-matrix.md)
