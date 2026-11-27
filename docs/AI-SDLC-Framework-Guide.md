# AI-SDLC Framework Guide

This guide explains how to set up and use the Capstone AI-SDLC framework, what
it does today, and how to extend it into the human-approved, independently
invoked agent workflow described below. It is written for both operators and
people new to software development.

> **Important distinction:** slash commands are Copilot Agent Skills in
> `.github/skills/`, not Python CLI subcommands. They guide Copilot to use the
> existing `python -m agentic_sdlc dev` stages and require conversational
> human decisions. Skill names are lowercase by the Agent Skills standard, so
> the requested `/devCode`, `/devtesting`, `/qualityCheck`, and `/devreview`
> appear as `/dev-code`, `/dev-testing`, `/quality-check`, and `/dev-review`.
> Digest-bound, persisted approvals are available only in the enterprise CLI
> workflow. The separate `dev code` and `dev testcase` draft outputs are not,
> by themselves, a single verification-bound artifact.

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
| Jira requirements | `dev backlog` reads configured Jira issues. `dev spec ISSUE` creates a hashed requirement draft; the main `run` flow can also read Jira when configured. | This guide does not verify the live Jira account, project permissions, or current backlog. A ticket needs acceptance criteria; `dev spec --criteria-file` can supply them if Jira has none. |
| Coding and tests | The main workflow runs coding and test generation for each requirement. Enterprise workflow generation creates isolated code and test drafts, integrates them into a manifest, and pauses for human approval before testing. | Main `run` generates code and tests concurrently. Enterprise code/test generation is independent but concurrent; inspect and approve the combined artifact before execution. Test files follow the repository's existing framework; the agent does not install dependencies or rewrite protected framework configuration. |
| Testing | Main `run` invokes detected or configured build, lint, and test commands and routes failures through bounded triage/fix loops. Enterprise verification supports exact artifact-bound outcomes. | The regular main workflow executes the repository's configured test suite, not a separately isolated test command for each acceptance criterion. Enterprise verification evidence must come from an independent trusted verifier; agent generation itself does not execute the generated code. |
| Security and pattern review | Main `run` gathers security and pattern findings, combines them with an LLM review, and scores the consensus. | Review results are not ground truth. Independent adjudication is required to measure missed bugs and false findings reliably. |
| Human approvals | Enterprise lifecycle now has digest-bound requirement, integrated development, independent quality, and final review decisions. The skills separate artifact generation, testing, quality decision, and review. | Conversational approval for standalone drafts is not a signed lifecycle receipt. `--interactive` is not a prompt before every internal agent action. A CLI actor name alone is not identity authentication; use a trusted operator environment and approval policy. |
| Merge, CI, deployment | Main workflow supports dry-run/Git/Gerrit integration, a regression command or detected checks, and per-environment deployment gates. | Defaults are intentionally conservative: VCS and deployment are dry-run. A dry-run is not a merge or deployment. |
| Enterprise measurements | `dev score`, `dev metrics`, `dev compare`, workflow receipts, and reports support evidence-based scoring and comparisons. | Missing or unlabelled evidence remains unknown; generated scores alone do not prove product quality or productivity improvement. |

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

For regular commands, run Python from the project virtual environment:

```bash
./.venv/bin/python -m agentic_sdlc --help
./.venv/bin/python -m pytest -q
```

The examples below use `./.venv/bin/python`; on Windows use
`.\.venv\Scripts\python.exe`.

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

## 5. Jira intake and requirements

### Read Jira backlog

After configuring Jira in `.env`, use:

```bash
./.venv/bin/python -m agentic_sdlc dev backlog
./.venv/bin/python -m agentic_sdlc dev backlog --summary
```

`backlog` is a read operation. It needs a Jira base URL, JQL query, and
credentials. The CLI loads the project `.env`; a policy/config YAML can also
be supplied with `--config`. For a fully offline exercise, configure
`jira.issues_file` to a Jira export instead.

### Draft a requirement from one ticket

```bash
./.venv/bin/python -m agentic_sdlc dev spec CP-101 --store .agentic_sdlc/specs.db
```

Use the actual Jira issue key in place of `CP-101`. The command produces a
draft and digest; persistence does **not** approve the requirement or authorize
development. Inspect it, then use the exact run ID and digest printed by the
CLI to record an approval:

```bash
./.venv/bin/python -m agentic_sdlc dev status --store .agentic_sdlc/specs.db --run-id RUN_ID
./.venv/bin/python -m agentic_sdlc dev approve --store .agentic_sdlc/specs.db --run-id RUN_ID --spec-sha256 DIGEST --actor approver-id
```

Configure trusted approver identities and keys before relying on signed
enterprise decisions. To reject a draft, use `dev reject` with the same run ID
and digest plus `--actor` and `--reason`.
The development approver must have a distinct identity and key from the
requirement approver. The quality approver must be distinct from the
development approver and independent verifier.

The Jira adapter extracts bullets following an **Acceptance criteria** label.
If the ticket has none, do not let an agent invent product decisions. Provide
approved criteria in a JSON file using `dev spec ISSUE --criteria-file FILE`.
For example:

```json
{
  "issue": "CP-101",
  "acceptance_criteria": [
    "An authorized user can view the requested record.",
    "A missing record returns the documented not-found response."
  ]
}
```

The `issue` value must match the Jira issue key, and the criteria must be
distinct, non-empty strings. `dev spec` accepts this file only when Jira has no
explicit acceptance criteria. Inspect the resulting draft before approving
it. `dev context` can read an authorized
Confluence page; treat fetched content as untrusted reference material, not
approval.

## 6. Run the existing full workflow

The main workflow is the closest current end-to-end path. It writes into the
target repository, so use a disposable copy or clean feature branch. Supply
requirements in a supported file and specify an existing repository path:

```bash
./.venv/bin/python -m agentic_sdlc run \
  --requirements examples/requirements.md \
  --repo /path/to/disposable/repository-copy \
  --interactive \
  --llm stub \
  --vcs dry-run
```

The example requirements are calculator examples, not Spring PetClinic
requirements. For PetClinic, prepare a separate requirement file with an ID,
description, and explicit acceptance criteria, and run against a disposable
copy of `benchmarks/spring-petclinic`.

In the main workflow, the high-level sequence is:

```text
Jira / files -> human requirement sign-off -> knowledge base and plan
-> repository guardrails -> code + test generation -> build/lint/test
-> failure triage and bounded fixes -> security review -> pattern review
-> LLM/rules finding ensemble -> quality threshold and blocking-severity check
-> merge/integration gate -> CI/regression -> defect/fix loop
-> traceability + DoD + release notes -> deployment approval per environment
```

`--interactive` enables available prompts. Current configured automatic gates
are in `src/agentic_sdlc/sdlc.yaml`; the defaults automatically approve
requirements sign-off, merge, and deploy. Override them for a human-operated
run. Main `run` generates code and tests in parallel for each requirement,
then executes checks and reviews requirements in workflow order. It does not
pause for a person after every generated file or agent action.

The default review policy is a score threshold of `0.7`, blocks consensus
findings of `critical` or `high` severity, and allows at most two review-fix
iterations per requirement. Test/fix and CI/defect retry limits are configured
separately (`3` and `2` by default). Adjust these only through reviewed policy
configuration; do not lower blocking thresholds just to get a green result.

## 7. Enterprise `dev` workflow (separate from `run`)

Read [enterprise-implementation.md](./enterprise-implementation.md) before
using enterprise lifecycle commands. They are separate from the legacy full
`run` graph and use persistent SQLite records, exact artifact digests, and
independent verification evidence. The implementation checklist marks some
verification as incomplete or deferred; do not treat a demo fixture as proof
of live CI, security, or deployment.

Useful read/evaluation commands include:

```bash
./.venv/bin/python -m agentic_sdlc dev backlog
./.venv/bin/python -m agentic_sdlc dev spec CP-101 --store .agentic_sdlc/specs.db
./.venv/bin/python -m agentic_sdlc dev context 12345
./.venv/bin/python -m agentic_sdlc dev criterion-registry --config POLICY.yaml
./.venv/bin/python -m agentic_sdlc dev score --assessment ASSESSMENT.json --evidence-dir EVIDENCE --report-dir REPORT
./.venv/bin/python -m agentic_sdlc dev compare --baseline BASELINE.json --actual ACTUAL.json
```

The command names describe different jobs:

| Enterprise command | Purpose | Relation to main `run` |
|---|---|---|
| `dev backlog` | Read Jira work items. | Intake/context, not a full run. |
| `dev spec` | Draft a digest-bound requirement from one Jira issue. | Specification stage, with a separate approval decision. |
| `dev context` | Read authorized Confluence context. | Optional provenance/context, not approval. |
| `dev score` | Score independent evidence or recorded lifecycle evidence. | Evaluation/reporting; its eligibility score does not control legacy `run`. |
| `dev compare` | Compare equivalently measured baseline and actual work. | Research comparison, separate from delivery execution. |
| `dev workflow-start` / `workflow-resume` | Drive the journaled enterprise lifecycle. | A separate persistent lifecycle with approval pauses and evidence. |

The current enterprise workflow can be started as follows (use real local
paths and a configured independent verifier):

```bash
./.venv/bin/python -m agentic_sdlc dev workflow-start CP-101 \
  --store /path/to/trusted/specs.db \
  --config /path/to/trusted/POLICY.yaml \
  --repo /path/to/disposable/repository-copy \
  --out /path/to/separate/generated-output \
  --acceptance-dir /path/to/independent/acceptance-tests \
  --actor verifier-id
```

The command may pause for requirement approval, human participation (in
assisted mode), or integrated development approval. Use
`dev workflow-start --run-id RUN_ID` to reuse an already stored specification
and preserve its requirement approval. After approving the combined manifest,
`workflow-resume` runs independent checks and pauses for quality approval;
after that approval, it runs review and pauses for final review approval.
After final review approval it may pause for CI authorization or remote DEV
authorization according to policy. Resume a paused run with
`dev workflow-resume --store DB --run-id RUN_ID`, providing the applicable
authorization flags only when explicitly approved. Failed checks, blocking
review findings, and human rejections pause for a correction decision; add
`--authorize-correction` only after a human requests one bounded correction
cycle. Correction also requires the same model-provider authorization as the
original workflow and returns to development approval. The configured
correction limit still applies. The offline demo in
`examples/enterprise/demo.ps1` uses synthetic inputs and demonstrates policy
flow only; it is not independently executed software testing.

Approve or reject each digest only after reviewing the corresponding evidence:

```bash
./.venv/bin/python -m agentic_sdlc dev approve --store DB --run-id RUN_ID \
  --development-sha256 MANIFEST_SHA256 --stage development --actor APPROVER --config POLICY.yaml
./.venv/bin/python -m agentic_sdlc dev approve --store DB --run-id RUN_ID \
  --quality-sha256 VERIFICATION_SHA256 --stage quality --actor APPROVER --config POLICY.yaml
./.venv/bin/python -m agentic_sdlc dev approve --store DB --run-id RUN_ID \
  --review-sha256 REVIEW_SHA256 --stage review --actor APPROVER --config POLICY.yaml
```

Use the corresponding `dev reject` command with the same digest, stage,
configured actor, and a concise `--reason`. `--stage` is optional when a
digest flag uniquely identifies the decision; when supplied, it must match.
The workflow rejects stale evidence and archives decisions when a correction
creates a new artifact. `dev report --store DB --run-id RUN_ID` includes
current approval receipts, verification checks, review findings, attempt
timings, and first-pass/final scores when available.

## 8. Copilot slash commands and human checkpoints

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
| `/requirement CP-101` | `requirement/SKILL.md` | Read Jira and draft a digest-bound requirement using `dev spec`. | Confirm scope and criteria; persist approval/rejection against the exact specification digest. |
| `/dev-code RUN_ID` | `dev-code/SKILL.md` | Start integrated code/test generation for the approved specification. | Inspect and approve/reject the exact combined manifest digest. |
| `/dev-testing RUN_ID` | `dev-testing/SKILL.md` | Resume the approved run and execute independent verification. | Inspect recorded checks and evidence; then use `/quality-check` for the decision. |
| `/quality-check RUN_ID` | `quality-check/SKILL.md` | Review recorded quality results and the first-pass score. | Approve/reject the exact verification digest. |
| `/dev-review RUN_ID` | `dev-review/SKILL.md` | Run security, pattern, and configured model review after quality approval. | Inspect findings and approve/reject the exact review digest. |

All skills are independently triggerable, but they require the same approved
requirement/run identity and immutable artifact references where the CLI
supports them. The coordinator skill directs the process; it does not
programmatically dispatch other skills. A later agent must not silently
replace an earlier accepted artifact. The human can request a bounded
correction or stop at every stage. No agent may auto-approve its own work.

Standalone `dev code` and `dev testcase` commands still create separate
drafts; they are not the integrated manifest used by enterprise testing and
review. For digest-bound verification, use `dev workflow-start` /
`workflow-resume` and follow each recorded approval pause. The workflow's
first-pass score records check outcomes before quality approval; the final
score includes later approval and validation gates. Do not treat either score
as proof that all requirement criteria are satisfied without examining the
underlying checks and traceability evidence.

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

## 9. Metrics: what can and cannot be claimed

Collect at least these per-requirement values: approved acceptance criteria,
criteria with linked tests, criteria with test evidence, generated production
and test files, build/lint/test status, test/fix attempts, review findings by
severity and disposition, review retries, regression defects, human review
time, and final approval status.

Existing evidence is useful but has limits:

| Evidence/metric | Existing source | Interpretation |
|---|---|---|
| Stage events and durations | `events.jsonl`; inspect with `dev status --events FILE`. | Workflow history/timing. It is not a code-completion percentage. |
| Criteria-to-code/test trace and score | `traceability.md`, including the number of criteria traced and listed code/test files. | Traceability signal; not proof that tests are sufficient or correct. |
| Review findings and quality score | `review-<REQ>.json`; main policy uses weighted severity penalties and consensus. | Finding/threshold summary; consensus is not independently labelled truth. |
| Review precision/recall/F1 | `agentic-sdlc benchmark` or `dev metrics` with independent defect labels. | Meaningful only when labels are independently adjudicated and comparable. |
| Runtime guardrail blocks/escapes | `dev metrics` with uniquely identified, independently labelled observations. | Separates blocked violations from legitimate actions that were falsely blocked. |
| Artifact coverage / stage scores | Enterprise `dev score` with evidence references and mandatory gates. | Missing inputs remain unavailable; delivery eligibility is distinct from legacy `run`. |

Do not report “percent of code completed” just from generated file count or
lines changed. Prefer criterion-level coverage with a named denominator:
`criteria with verified passing tests / total approved criteria`. Keep
unverified criteria visible. “Bugs caught” should mean independently confirmed
defects detected, with false alerts and missed defects reported alongside it.
The enterprise comparison tool requires equivalent task/specification and
acceptance identities; its output is not, on its own, causal evidence.
Enterprise `dev report` now summarizes production/test files and changed lines
per attempt, independent check pass/failure counts, review findings, and
independently adjudicated defect counts when available. These are size and
evidence metrics, not a percent-complete estimate; without independent
adjudication, confirmed bug counts remain unavailable.

## 10. Run artifacts and when they appear

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

Enterprise `dev` workflows produce their own separate run artifacts, SQLite
receipts, manifests, digests, score reports, and timelines. Do not assume those
files are inside the legacy run directory or that they have the same approval
semantics.

## 11. Merge, regression, deployment, and release gates

For the main run, dry-run is the default. Git/Gerrit integration has a human
merge gate, and CI runs the configured `ci.command` or the repository's
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

The separate enterprise evaluator applies its own rubric and mandatory gates.
Those scores do not override the main workflow's merge or deployment approval.
The enterprise implementation checklist records known unfinished and deferred
verification; read it before claiming an end-to-end enterprise release gate is
fully verified.

## 12. Project structure

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

## 13. Spring PetClinic example

Use PetClinic as a **target application example**, not as a claim that the
repository already contains a PetClinic Jira ticket or that the live Jira
project is connected. First inspect the target project's conventions and use
a disposable copy. Ask the product owner for exact behaviors; for instance,
a request could be to validate a new owner contact field. The actual ticket
must specify permitted formats, required/optional behavior, error response,
and persistence expectations before code is generated.

Write one criterion per observable behavior, then map each to test scenarios:
valid input, invalid input, boundary values, and regression behavior. Keep the
acceptance tests in a trusted directory separate from generated output for the
enterprise workflow. Approve the specification, generate the production and
test drafts, inspect both diffs, run the project's own checks, review evidence,
and only then decide on merge. This is a usage pattern; it does not assert a
particular PetClinic feature or test result.

## 14. Further reading

- [Project overview and quick start](../README.md)
- [Enterprise workflow and implementation status](./enterprise-implementation.md)
- [Implementation requirements matrix](./implementation-requirements-matrix.md)
