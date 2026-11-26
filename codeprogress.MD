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

## New teammate setup

### 1. Install the tools and get the correct branch

Install [Git](https://git-scm.com/downloads) and [Python 3.11 or later](https://www.python.org/downloads/). On Windows, enable Python on PATH and open a new PowerShell window. Install Docker Desktop with the Linux container engine for the Docker tests. You can complete the first offline check without Docker, Jira credentials, or a model.

```powershell
git --version
python --version
git clone --branch implementcode https://github.com/pavi550/Capstone.git
Set-Location Capstone
git status --short --branch
.\setup_env.bat --smoke-only
$env:PYTHONPATH = 'src'
$py = '.\.venv\Scripts\python.exe'
& $py -m agentic_sdlc dev workflow-start --help
```

Run all later commands from the repository root, where `pyproject.toml` and `setup_env.bat` are located. The setup script creates `.venv` and installs the project and dependencies. Expect Git status to show `implementcode`, the offline smoke test to pass, and the CLI to show workflow help. For an existing clean clone, use `git switch implementcode` and `git pull --ff-only origin implementcode`. On macOS or Linux, use `bash setup_env.sh --smoke-only` and `PYTHONPATH=src ./.venv/bin/python` instead.

### 2. Configure Jira and optional Confluence access

Ask for browse and create access to an approved **Jira sandbox** project. Create a token for your own authorized Atlassian account using the [Atlassian API token guide](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/). This project's Jira adapter uses the site URL, account email, and token. If your organization requires a scoped token with a different API URL, get compatible connection settings from the project administrator.

Run `.\setup_env.bat`, choose **Configure services only**, then choose Jira or Jira and Confluence. You can also edit `.env` in the repository root. If the file is absent, create it from [.env.example](.env.example). Preserve any existing values.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
git check-ignore .env
```

Enter your actual settings locally. Here `TEST` is a placeholder for your sandbox project key. Match the status to the sandbox's real workflow.

```dotenv
JIRA_BASE_URL=https://your-team.atlassian.net
JIRA_EMAIL=your-account-email
JIRA_API_TOKEN=your-local-token
JIRA_JQL='project = "TEST" AND status = "To Do"'
```

For optional Confluence reads, set `CONFLUENCE_BASE_URL` to the site's `/wiki` URL, then set `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN`. Confirm you can open the intended test page with that account. The `git check-ignore` command must print `.env`. Keep token values in `.env` or your process environment. A policy can refer to environment variable names. Keep the tokens themselves out of policy files, Git, Jira descriptions, and shared logs.

### 3. Create the Jira test issue

1. Open the approved sandbox project in Jira. Click **Create (+)**, select the sandbox project or space, and select **Task**.
2. Enter the title **Capstone sandbox: subtract two numbers**. Paste the text below into **Description**. Keep the `Acceptance Criteria:` heading and bullet list.
3. Click **Create**. Open the saved issue and set its status to **To Do** if that is what your JQL selects. Copy the issue key, such as `TEST-42`. Use the key in commands. CP-4 remains blocked.
4. If testing Confluence, add a link to a test page your account can read. Keep the explicit acceptance criteria in Jira.

```text
In the disposable calculator project, implement or preserve
calculator.subtract(left, right) so it returns left minus right.

Acceptance Criteria:
- subtract(7, 2) returns 5.
- subtract(2, 7) returns -5.
- subtract(4, 4) returns 0.
- Existing calculator regression tests still pass.
```

For screenshots of the Create dialog, see the [Jira Cloud work-item guide](https://support.atlassian.com/jira-software-cloud/docs/create-a-work-item-and-a-subtask/). Create the issue in the browser. The test commands read the issue.

```powershell
$issue = 'TEST-42' # Replace with the key Jira gave you.
& $py -m agentic_sdlc dev backlog --summary
& $py -m agentic_sdlc dev spec $issue
```

The backlog should list the issue if it matches JQL. The spec preview should show its key and the four acceptance criteria. This preview does not create a stored workflow. If the issue is absent from the backlog but spec works, check its status and JQL. If spec is blocked with missing criteria, edit the Jira description and preview it again. If using Confluence, `& $py -m agentic_sdlc dev context $issue` shows linked context.

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

Complete the mocked check and Docker fixtures above before trying a real model on a Jira issue. This walkthrough uses the calculator fixture as a disposable source, a loopback model, local Docker verification, and local DEV checks. Keep remote CI and remote DEV authorization flags unset.

**Configure the model.** Use an approved OpenAI-compatible loopback endpoint. For Ollama installation and model setup, follow the [README](README.md) section on the real local Ollama test. Start the service and check `ollama list` for the installed model name. Set these values in your existing `.env` for an Ollama-backed test.

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=replace-with-installed-model-name
LLM_API_KEY=ollama
```

**Create the local test policy.** Run this block in the same PowerShell session where you set `$py` and `$issue`. It copies the disposable source and acceptance tests into separate directories and creates distinct keys for two test operator identities. Keep this window open because the keys and variables are session-scoped. For team sign-off, keep the approver and verifier keys under separate operator control.

```powershell
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ('capstone-jira-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $sandbox | Out-Null
$repo = Join-Path $sandbox 'source'
$acceptance = Join-Path $sandbox 'acceptance'
Copy-Item examples/enterprise/actual-fixture $repo -Recurse
Copy-Item examples/enterprise/acceptance $acceptance -Recurse
$store = Join-Path $sandbox 'workflow.db'
$out = Join-Path $sandbox 'out'
$policy = Join-Path $sandbox 'policy.json'
$env:CAPSTONE_TEST_APPROVER = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:CAPSTONE_TEST_VERIFIER = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$image = docker image inspect capstone-verifier-local:latest --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw 'Build the local verifier image in the Docker fixture step first.' }
$pytest = @('/usr/local/bin/python', '-P', '-m', 'pytest', '-q', '-c', '/dev/null', '-p', 'no:cacheprovider')
@{
    enterprise = @{
        approvers = @{ reviewer = 'CAPSTONE_TEST_APPROVER' }
        verifiers = @{ verifier = 'CAPSTONE_TEST_VERIFIER' }
        container = @{
            image = $image
            commands = @{
                acceptance = $pytest + @('/acceptance')
                generated = $pytest + @('/artifact/tests', '--ignore=/artifact/tests/test_regression.py')
                regression = $pytest + @('/artifact/tests/test_regression.py')
                quality = @('/usr/local/bin/python', '-I', '-m', 'ruff', 'check', '--isolated', '--no-cache', '/artifact')
                security = @('/usr/local/bin/python', '-I', '-m', 'bandit', '-q', '-r', '-x', '/artifact/tests', '/artifact')
                definition_of_done = @('/usr/local/bin/python', '-I', '-c', 'from pathlib import Path; assert Path("calculator/__init__.py").is_file()')
                dev = @('/usr/local/bin/python', '-P', '-c', 'from calculator import subtract; assert subtract(2, 7) == -5')
            }
        }
    }
} | ConvertTo-Json -Depth 12 | Set-Content -Encoding ascii $policy
```

**Start the workflow.** Confirm that `$issue` is the sandbox Jira key and that `LLM_BASE_URL` points to the approved loopback endpoint before using `--allow-provider`.

```powershell
& $py -m agentic_sdlc dev workflow-start $issue --store $store --config $policy --repo $repo --out $out --acceptance-dir $acceptance --actor verifier --allow-provider
```

Expect the stage `requirements` and status `WAITING_FOR_APPROVAL`. Read the stored requirements and copy the returned `run_id` and `specification_sha256`. If the workflow is blocked, inspect the draft and correct the sandbox issue or settings before trying again.

```powershell
$runId = 'PASTE_RUN_ID'
$specDigest = 'PASTE_SPECIFICATION_SHA256'
& $py -m agentic_sdlc dev status --store $store --run-id $runId
& $py -m agentic_sdlc dev approve --store $store --run-id $runId --stage requirements --spec-sha256 $specDigest --actor reviewer --config $policy
& $py -m agentic_sdlc dev workflow-resume --store $store --run-id $runId --allow-provider
```

Resume runs parallel coding and test generation, integration, verification, and review. It may make a bounded correction. If it returns `WAITING_FOR_APPROVAL` at `review`, inspect the checks and findings and copy the current `review_sha256`. A model can fail the checks, so a successful result is conditional on the generated evidence. Export a report for a failed or blocked run. Approve only the current review for a passing attempt.

```powershell
$reviewDigest = 'PASTE_CURRENT_REVIEW_SHA256'
& $py -m agentic_sdlc dev approve --store $store --run-id $runId --stage review --review-sha256 $reviewDigest --actor reviewer --config $policy
& $py -m agentic_sdlc dev workflow-resume --store $store --run-id $runId --allow-provider
```

**Read and retain the evidence.** A passing local smoke check produces `LOCAL_DEV_VALIDATED`. Inspect the final score's separate delivery eligibility field before treating the result as deliverable. For a paused, failed, or successful run, export to a fresh directory.

```powershell
$report = Join-Path $sandbox ('report-' + [guid]::NewGuid().ToString('N'))
& $py -m agentic_sdlc dev report --store $store --run-id $runId --report-dir $report
Get-ChildItem $report
```

Read `report.md` for the summary and `report.json` for checks, artifact digests, stage attempts, approvals, scores, and historical evidence. To request cancellation, run `& $py -m agentic_sdlc dev cancel --store $store --run-id $runId`. An unresolved cleanup or external operation is a separate condition that must be investigated.

### 4. Troubleshoot common first-run issues

| Symptom | Check |
|---|---|
| `workflow-start` is unknown | Check the branch and run from the repository root with `$env:PYTHONPATH = 'src'` and the local `.venv` interpreter. |
| Jira returns 401, 403, or 404 | Verify the site URL, email, token, issue key, and Browse permission. |
| Jira item is absent from the backlog | Confirm the sandbox project key and status in `JIRA_JQL`. Try the direct spec preview to separate a filter problem from an access problem. |
| Model connection fails | Check the service is running, the model is installed, and the configured URL points to your approved loopback endpoint. Use the same provider flag on start and resume. |
| Docker verification stops before checks run | Confirm `docker info --format '{{.OSType}}'` returns `linux`, the policy contains the exact `sha256:` image ID, and trusted tests are outside source and output. |
| Approval refers to a stale digest | Copy the latest digest from the correct run and stage. An older attempt's review cannot approve a revised artifact. |
| Verification, review, or DEV is blocked | Inspect the latest report and test output digests. A missing required independent label or CI result does not count as a pass. |

### 5. Additional integration paths

For the dedicated integration phase, prepare a trusted policy file, a supported test issue with acceptance criteria or an offline issue fixture, separate source/output/acceptance directories, a pinned verifier image, and separate approver and verifier identities. For local independent review, configure `enterprise.delivery_policy.local_review.reference_dir` outside the evaluated workspace. Independent reference files are named by manifest digest and bind the exact review and specification. If this evidence is required and missing, the workflow pauses. Use an explicitly approved model and add `--allow-provider` on start and resume only for authorized provider execution.

For the controlled live phase, use `--authorize-ci` and `--authorize-remote-dev` only when approved for the configured private CI repository and DEV environment. The [policy setup and commands](docs/enterprise-implementation.md) list those prerequisites. Uncertain writes require reconciliation before proceeding.

For a prepared research experiment, select the trial with `--experiment` and `--trial` at workflow start. Use `dev experiment-report --directory <TRIALS> --report-dir <NEW-REPORT-DIRECTORY>` for comparable JSON, CSV and Markdown. Submit independently verified human evidence through `dev experiment-baseline`. A synthetic fixture or missing baseline is not a research result.
