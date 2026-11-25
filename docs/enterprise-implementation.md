# Enterprise implementation checklist

Starting branch `implementcode` at `e70d1f6`. Working tree was clean. The
baseline suite passed 116 tests and skipped the opt-in Ollama smoke test.
The final suite passed 128 tests and skipped the same opt-in test. Ruff,
Bandit, compilation, and Git whitespace checks passed.
The existing state machine, parallel code/test generation, Jira client,
MCP provider router, and reviewer benchmark were inspected and retained.

| Area | Implemented in this increment | Still open |
| --- | --- | --- |
| Enterprise intake | Paginated Jira Cloud and offset search, bounded GET retries, ADF acceptance criteria extraction, direct issue lookup, explicit Confluence page snapshots with provenance | Webhooks, polling, deduplication, attachment and comment retrieval, Confluence discovery and RAG, GitHub experiment evidence ingestion, authorized publication |
| Orchestration and evidence | Existing graph records a monotonic duration and attempt for each node exit, including failed nodes. CLI drafts a hashed specification with criterion IDs and optional page provenance. SQLite stores drafts and digest-bound operator decisions across restarts. | Full workflow persistence and resume, independent code/test/review commands, authenticated approval authority and enforcement in the workflow |
| Runtime and independent verification | Existing guardrails and dry-run operation remain. HTTP clients with headers refuse redirects. Assessment gate results can block delivery eligibility. | Trusted execution isolation, independent CI artifacts and attestations, enforced DEV validation for the new spec flow |
| Evaluation and research | Configurable 100-point rubric, evidence-digest checks, provisional scoring, mandatory gate results, JSON/CSV/Markdown score output, matched baseline comparisons, an optional OPI, and metrics from independently labelled observations | Automatic first-pass/final capture, collection of independent guardrail labels, controlled ground-truth experiments, active human timing, token and cost instrumentation, sensitivity study tooling, repeated experiments |
| Verification | Baseline suite run. New tests cover intake, scoring, persisted decisions, and metrics. A live GitHub remote HEAD read succeeded. | Live Jira and Confluence were blocked by missing local credentials. No live provider experiment or external writes were performed |

## Continuation checklist

Baseline at the start of this continuation on `implementcode` was 128 passed,
1 skipped. Earlier local changes were retained. Final regression:
136 passed, 1 skipped (opt-in Ollama). Ruff, Bandit, compilation, and
Git whitespace checks passed. The checked-in demo completed with
one development attempt. A live GitHub remote HEAD read succeeded.
The workspace has no root `.env` or Jira/Confluence credentials in the
process environment. Live Atlassian reads remain blocked by credentials.
No paid LLM calls or external writes were performed.

| Requirement | Status | Implementation and verification | Boundary or remaining work |
| --- | --- | --- | --- |
| Digest-bound requirement approval and final review | OFFLINE_E2E_VERIFIED | `approval.py`, `specifications.py`, `verification.py`; `tests/test_development.py` rejects unsigned approvals, wrong keys, stale review digests, and missing final approval | Local HMAC possession only. Run on a trusted host and protect policy, DB, lock files, and artifact directories. Enterprise identity and remote attestation remain open. |
| Pause, resume, attempts, and cancellation | INTEGRATION_TESTED | `specifications.py` holds a per-run SQLite lock, persists attempts, records interrupted attempts, bounds retries and supports cooperative cancellation. Recovery and concurrent-run tests in `tests/test_development.py`. | Scope is development staging. Cancellation blocks publication of staged evidence; work already in progress can finish. No automatic replay of real external writes. |
| Parallel isolated code and test generation | OFFLINE_E2E_VERIFIED | `development.py` calls the existing agents with separate copies of the same bounded source snapshot. Barrier test verifies actual concurrency and isolated writes. Merged files are checked against a SHA-256 manifest on resume. | Internal stub only. Use a trusted source directory, not a hostile filesystem. This is file-level isolation, not an OS sandbox for real generated code. |
| Independent verification, review, and local validation | OFFLINE_E2E_VERIFIED | `verification.py` binds a separate verifier's attested checks to the exact integrated manifest, runs the existing static/stub review, enforces final approval, and records `OFFLINE_VALIDATED`. A failed independent check blocks review in acceptance tests. | The CLI imports attested outcomes and does not run test commands in the agent process. Trusted independent execution, real DEV validation, coverage collection, GitHub Actions ingestion, and correction cycles remain open. |
| Stage CLI and lifecycle reporting | INTEGRATION_TESTED | `dev run`, `resume`, `cancel`, `verify`, `review`, `validate`, `report`, and review digest support in `approve`. `dev report` returns historical receipts, attempt durations, and requirement approval waiting time. | Individual code and testcase execution, testing in a sandbox, active human review sessions, first-pass/final score automation, and live provider usage remain open. |
| Jira automation and automatic Confluence retrieval | NOT_STARTED | Existing read-only/manual paths are unchanged. | Jira event authentication, deduplication, polling, and automatic context retrieval still need implementation. |
| Integrated scoring and controlled trials | NOT_STARTED | Existing independent score, metrics, and comparison commands remain covered by `tests/test_enterprise.py`. | Lifecycle receipts are separate from evaluator assessments. No new experimental results, cost capture, automated baseline task runs, or productivity claims. |

## Run the offline approval lifecycle

Run the [PowerShell demonstration](../examples/enterprise/demo.ps1) from
the repository root with project dependencies installed:

```powershell
powershell -NoProfile -File examples/enterprise/demo.ps1
```

It creates a temporary SQLite store and policy, uses separate synthetic
keys, then runs `dev spec`, `dev approve`, `dev run`, `dev resume`,
`dev verify`, `dev review`, review approval, `dev validate`, and `dev report`.
It uses the sample source and explicit synthetic verifier outcomes.
Expected progression is `AWAITING_APPROVAL`, `APPROVED`,
`READY_FOR_VERIFICATION`, `PASSED`, `WAITING_REVIEW_APPROVAL`,
and `OFFLINE_VALIDATED`. Only one development attempt should be recorded.

For manual runs, set `enterprise.approvers.<actor>` and
`enterprise.verifiers.<actor>` in a trusted operator YAML to environment
variable names holding independently generated keys of at least 32 bytes.
Pass `--config` to authenticate the requirement decision. Legacy unsigned
decisions remain viewable but cannot unlock development. Keep the SQLite
store and keys outside source and generated directories. The verifier JSON
must include `run_id`, `spec_sha256`, `manifest_sha256`,
`environment: OFFLINE_SIMULATION`, and all six Boolean checks:
`acceptance`, `regression`, `security`, `guardrails`,
`definition_of_done`, and `dev`. The synthetic example demonstrates
policy enforcement, not independently executed CI or a deployment.

## Reproduce the offline evidence path

From the repository root, use Python from the existing Capstone virtual
environment. The following examples use a fresh output directory. The
fixture uses invented tickets and test outcomes. They are demonstration
inputs and do not constitute research findings.

```text
python -m agentic_sdlc dev backlog --config examples/enterprise/offline.yaml
python -m agentic_sdlc dev spec DEMO-1 --config examples/enterprise/offline.yaml
python -m agentic_sdlc dev score --assessment examples/enterprise/assessment.json --evidence-dir examples/enterprise --report-dir .agentic_sdlc/enterprise-demo
```

Expect one synthetic backlog item, a draft with two criterion IDs and
`AWAITING_APPROVAL`, and a **provisional score of 4.5/100** with 5 points
evaluated. The explicitly failed independent-tests gate marks delivery
`BLOCKED`. The score command produces `score.json`, `score.csv`, and
`score.md`. A live Jira configuration sets `jira.enabled`, `jira.base_url`,
`jira.jql`, and `jira.search_path: /rest/api/3/search/jql`. Configure the
environment variable names in `jira.user_env` and `jira.token_env` for the
account used to read the backlog. No writes are issued by these commands.

For one explicitly authorized Confluence page, set `confluence.base_url`
in the YAML and supply `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` in the
environment. Use `python -m agentic_sdlc dev context 12345 --config my.yaml`
for provenance. An optional `--output` saves the bounded storage body to a
new file as untrusted data. Use `dev spec ... --page-id 12345` to record that
page revision in a draft. The draft has no approval authority.

To persist a draft, use `dev spec DEMO-1 --config examples/enterprise/offline.yaml --store spec.db`
in a trusted operator directory. The command prints a run ID and the draft
SHA-256. Copy those values into the following commands.

```text
python -m agentic_sdlc dev status --store spec.db --run-id RUN_ID
python -m agentic_sdlc dev approve --store spec.db --run-id RUN_ID --spec-sha256 DIGEST --actor reviewer-id
```

For rejection, run the same command with `reject` and a `--reason`.
The decision is transactional and immutable. An identical retry is
idempotent, and missing criteria block approval. The actor is an
operator-supplied identifier. Protect the DB with filesystem permissions;
the local CLI does not authenticate the actor or gate the legacy workflow.
For legacy run evidence, use `dev status --events path/to/events.jsonl`
to inspect recorded timings and the terminal stage when available.

## Verify legacy workflow timings locally

The existing stub workflow can be exercised separately on a disposable
copy of `examples/sample_repo`. From the repo root in PowerShell:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
$demo = Join-Path ([System.IO.Path]::GetTempPath()) ('capstone-flow-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $demo | Out-Null
$sample = Join-Path $demo 'sample_repo'
Copy-Item -LiteralPath examples/sample_repo -Destination $sample -Recurse
$runs = Join-Path $demo 'runs'
python -m agentic_sdlc run --requirements examples/requirements.md --repo $sample --out $runs --llm stub --vcs dry-run
$log = Get-ChildItem -LiteralPath $runs -Filter events.jsonl -Recurse | Select-Object -First 1
python -m agentic_sdlc dev status --events $log.FullName
```

The sample run ends at `end` with two scaffold requirements marked
approved under the legacy offline configuration. This exercises the
existing graph and timing capture. It uses automatic approvals and the
stub LLM, and does not validate enterprise approval enforcement or
real software quality.

## Independent assessment and baseline inputs

Set weights under `evaluation.rubric.criteria` and version the rubric
when weights or criteria change. Weights across all stages must total
100. Use `evaluator_version`, criterion `ratio`, `method`, `reason`,
and file references with relative `path` and SHA-256 in the assessment.
Gate references have a boolean `passed` plus supporting artifacts.
Missing measurements stay unevaluated. Digests bind the manifest to
evidence bytes; operators must still obtain the manifest and evidence
from an independent trusted evaluator. A gate result here evaluates
delivery eligibility and does not control the legacy orchestrator.

Use `python -m agentic_sdlc dev compare --baseline baseline.json --actual actual.json`
with `mode`, `task_id`, `repository`, `starting_commit`, `spec_sha256`,
and `acceptance_sha256`. The baseline mode is `HUMAN_BASELINE`, and
the other mode is `AGENT_ASSISTED` or `AGENT_DRIVEN`. Supply measured
`delivery_seconds`, `human_seconds`, and `total_cost` with a matching
`currency` as available. Reduction is `(baseline − actual) / baseline × 100`.
Missing inputs or zero denominators yield null. A negative reduction shows
a regression; these comparisons establish no causal effects on their own.


The optional OPI requires both records to use a matching deliverable-only
rubric revision. Supply `deliverable_quality` as an object with `score`
(0 to 100) and `rubric_version`. Keep human-effort and rework criteria out
of this score. The efficiency components use
`100 * baseline / (baseline + actual)`. Weights default to quality 40%,
delivery time 25%, human effort 25%, and cost 10%. Adjust
`evaluation.index_weights` in a separate YAML to compare weight choices.
Missing inputs yield a null index. OPI is a composite score, not a
percentage productivity improvement.


Use `dev metrics --labels assessments.json` for independent labels. Supply a
`guardrails` array with each observation's `id`, `phase` (`runtime` or
`post_execution`), `violation` (boolean or null for unlabelled), and
`decision` (`BLOCKED`/`ALLOWED` or `DETECTED`/`MISSED`, respectively).
Supply `defects` as a list of independently verified defect IDs and
`findings` with `id`, `origin` (`ai`, `human`, or `tool`),
`adjudication` (`confirmed`, `incorrect`, or `unverified`), and a
`defect_id` for confirmed findings. Identify repeated evidence with
`derived_from` so it does not count as an independent discovery.
