# Capstone code progress and teammate handoff

This is the handoff for `pavi550/Capstone`, branch `implementcode`. Start here to understand how the code works, reproduce a safe end-to-end run, and pick up unfinished work.

**Status:** The basic unified SDLC flow is implemented. A disposable test exercised the real CLI, orchestration, and SQLite code with deterministic model replies and a mocked Docker boundary. It covered both approval pauses, one correction, re-review, DEV validation receipt handling, scoring, and reporting. Live enterprise and comprehensive verification are pending. CP-4 is still blocked and is not the test fixture.

The mandatory implementation matrix records **22 of 105 implemented and 83 partial**. These are coding statuses, not test results. Partial rows can have usable paths and still lack mandatory functionality.

- [Requirement matrix and exact remaining gaps](docs/implementation-requirements-matrix.md)
- [Implementation checklist, evidence history, and policy details](docs/enterprise-implementation.md)
- [Installation and general project usage](README.md)

## How the code fits together

`Jira or fixture → Confluence context → requirement draft → requirement approval → parallel code and tests → integration → verification → review and independent evaluation → bounded correction if needed → final approval → DEV validation → scores and reports`

`dev workflow-start` creates the stored run. `dev workflow-resume` advances eligible stages with the same run ID. A pause at an approval gate is expected. The approver signs the exact specification or review digest with a configured actor identity. The verifier has a separate identity. Artifact changes create a new attempt and move earlier receipts into history. Remote CI and remote DEV require separately enabled policy and explicit authorization. Independent stage commands remain available for troubleshooting.

| Area | Code and supporting paths | Implemented / remaining |
|---|---|---|
| Lifecycle and agents | [CLI](src/agentic_sdlc/cli.py), [orchestrator](src/agentic_sdlc/orchestrator.py), [SQLite store](src/agentic_sdlc/specifications.py), [lifecycle journal](src/agentic_sdlc/lifecycle.py), [agents](src/agentic_sdlc/agents), [development](src/agentic_sdlc/development.py), [approvals](src/agentic_sdlc/approval.py) | Unified resume, approval gates, isolated parallel generation, artifact manifests, checkpoints and bounded corrections are connected. Remaining work includes stopping underlying blocked transport calls and proving ownership of abandoned subprocesses. Start at WS-1-006 and WS-1-007. |
| Trusted CI | [Protected workflow](.github/workflows/trusted-evaluator.yml), [GitHub client](src/agentic_sdlc/github_evidence.py), [evidence validation](src/agentic_sdlc/delivery_evidence.py), [verification](src/agentic_sdlc/verification.py) | Authorized publication, exact run monitoring, signed archive verification, review evidence, and read-only dispatch reconciliation are connected. Unconfirmed bundle publication still requires reconciliation; live protected-workflow behavior remains to be verified. |
| Rubric and review | [Scoring engine](src/agentic_sdlc/evaluation.py), [verification and review receipts](src/agentic_sdlc/verification.py), [metrics](src/agentic_sdlc/metrics.py) | Existing 100-point rubric, first-pass and final scoring, independent review metrics, and delivery gates. More criterion-specific automatic collectors remain. Missing independent evidence must stay unevaluated. |
| Provider governance | [Provider adapters](src/agentic_sdlc/llm.py), [budget ledger](src/agentic_sdlc/provider_budget.py), [configuration](src/agentic_sdlc/sdlc.yaml) | OpenAI-compatible, Anthropic and Gemini paths, shared reservations, usage normalization and estimates. Interrupted reservations retain their charges. Remaining work includes some budget scopes, fallback restrictions and exceptional accounting cases. |
| Research and reporting | [Experiments](src/agentic_sdlc/experiments.py), [seeded scenarios](src/agentic_sdlc/scenarios.py), [comparison formulas](src/agentic_sdlc/evaluation.py) | Human baseline, agent-assisted and agent-driven paths, trial policy binding, independent baseline submission and JSON/CSV/Markdown output. Remaining work includes scenario coverage, managed evidence completeness and recovery. Measured human baselines are still required for real comparisons. |
| DEV validation | [Broker and adapters](src/agentic_sdlc/remote_dev.py), [Docker verifier](src/agentic_sdlc/container_verifier.py), [trusted harness](examples/enterprise/verifier-image/dev_harness.py) | Local verification and post-approval DEV validation, broker worker and Docker/Kubernetes paths. Broker restart reconciliation and cleanup on interrupted operations remain partial. |
| Jira and Confluence | [Ingress, queue, renewal](src/agentic_sdlc/intake.py), [Jira adapter](src/agentic_sdlc/jira.py), [Confluence adapter](src/agentic_sdlc/confluence.py), [setup](setup_env.py) | Backlog, signed administrator webhook and relay intake, polling, discovery and approval-gated drafting. Hosted and manual drafting share a worker lock. Worker recovery, renewal and shutdown edge cases remain. |

## Test in this order

Run from the repository root. Use separate temporary storage, a disposable source repository, and a test issue. Keep secrets in the ignored `.env` or approved process environment. [.env.example](.env.example) lists configuration names. Do not publish bundles or enable live credentials for the local checks below.

### 1. Setup and local contract checks

```powershell
.\setup_env.bat --smoke-only
$env:PYTHONPATH = 'src'
$py = '.\.venv\Scripts\python.exe'
& $py -m pytest -q tests/test_actual_verification.py -k disposable_unified
& $py -m ruff check src
& $py -m compileall -q src
git diff --check
```

The targeted test uses synthetic credentials and mocked provider and Docker boundaries. Its expected result is one passing test. For the later comprehensive phase, run `& $py -m pytest -q -rs` and `& $py -m ruff check .`. The full regression suite does not establish live integration.

### 2. Disposable actual local Docker fixtures

Use a Linux Docker engine. The image build may need access to package registries. The verifier runs target checks in restricted containers. The fixture uses synthetic inputs, so it does not establish actual model or research performance.

```powershell
docker build -t capstone-verifier-local:latest examples/enterprise/verifier-image
$image = docker image inspect capstone-verifier-local:latest --format '{{.Id}}'
powershell -NoProfile -File examples/enterprise/demo-actual.ps1 -Image $image -FixtureApprovals
powershell -NoProfile -File examples/enterprise/demo-actual.ps1 -Image $image -FixtureApprovals -FailAcceptance
```

Expected: the success fixture reaches local DEV validation; the negative fixture records blocking acceptance evidence and cannot proceed to delivery. For interactive digest entry on a disposable fixture, omit `-FixtureApprovals`. See the [fixture runner](examples/enterprise/demo-actual.ps1) and [fixture inputs](examples/enterprise).

### 3. Unified end-to-end run with independent human approvals

For the dedicated integration phase, prepare a trusted policy file, a supported test issue with acceptance criteria or an offline issue fixture, separate source/output/acceptance directories, a pinned verifier image, and separate approver and verifier identities. For local independent review, configure `enterprise.delivery_policy.local_review.reference_dir` outside the evaluated workspace. Independent reference files are named by manifest digest and bind the exact review and specification. If this evidence is required and missing, the workflow pauses. Use an explicitly approved model and add `--allow-provider` on start and resume only for authorized provider execution.

```powershell
& $py -m agentic_sdlc dev workflow-start <TEST-ISSUE> --store <DB> --config <POLICY> --repo <SOURCE> --out <OUTPUT> --acceptance-dir <TRUSTED-TESTS> --actor <VERIFIER>
& $py -m agentic_sdlc dev status --store <DB> --run-id <RUN-ID>
& $py -m agentic_sdlc dev approve --store <DB> --run-id <RUN-ID> --stage requirements --spec-sha256 <SPEC-DIGEST> --actor <APPROVER> --config <POLICY>
& $py -m agentic_sdlc dev workflow-resume --store <DB> --run-id <RUN-ID>
& $py -m agentic_sdlc dev approve --store <DB> --run-id <RUN-ID> --stage review --review-sha256 <REVIEW-DIGEST> --actor <APPROVER> --config <POLICY>
& $py -m agentic_sdlc dev workflow-resume --store <DB> --run-id <RUN-ID>
& $py -m agentic_sdlc dev report --store <DB> --run-id <RUN-ID> --report-dir <NEW-REPORT-DIRECTORY>
```

Replace every angle-bracket placeholder before running. Follow the returned stage and status instead of applying an approval to a blocked attempt. Use the digest from the current pause. If a check fails, inspect the report and historical receipts. A correction must be evaluated against the new manifest and review. In the controlled live phase, use `--authorize-ci` and `--authorize-remote-dev` only when approved for the configured private CI repository and DEV environment. The [policy setup and commands](docs/enterprise-implementation.md) list those additional prerequisites. Uncertain writes require reconciliation before proceeding.

For a prepared research experiment, select the trial with `--experiment` and `--trial` at workflow start. Use `dev experiment-report --directory <TRIALS> --report-dir <NEW-REPORT-DIRECTORY>` for comparable JSON, CSV and Markdown. Submit independently verified human evidence through `dev experiment-baseline`. A synthetic fixture or missing baseline is not a research result.
