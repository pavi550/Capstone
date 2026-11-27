# Enterprise implementation checklist

The sequential closure pass tracks pending clauses in [the requirements matrix](implementation-requirements-matrix.md). The matrix contains a clause extraction and an initial lifecycle audit. Finish auditing the individual entries before implementing additional functionality. The first unresolved lifecycle entry is `WS-1-006`.

## Workstream 1 recovery checkpoint

`dev recover --store DB` scans persisted running stages. An exclusive SQLite run
lock distinguishes an active owner from an abandoned stage. Recovery records the
decision in `lifecycle_recovery` and exposes it through `dev report`. Uncertain
external operations require reconciliation. The command only assesses them.
`dev spec ISSUE --store DB --intake-run-id ID` resumes a known intake without
creating a new specification identity. Existing stage records remain readable
after the additive migration. `lifecycle_intervals` stores worker PID, last
recorded timestamp and monotonic duration when available.

Workstream 1 remains partially implemented. Open entries are WS-1-003,
WS-1-005 through WS-1-019. See the matrix for the exact remaining behavior.
Focused checks passed (20 tests), along with Ruff, compilation and Git whitespace
checks. Comprehensive verification remains pending.

## Workstream 1 child checkpoint progress

Parallel generation persists independent output manifests in SQLite and reuses
a completed child only when approved inputs and current artifact digests match.
Trial registration is atomic. Use `dev cancel --lifecycle-stage STAGE` for a
journaled stage (with the regular run selector), or `dev experiment-cancel
--store DB --experiment-id ID` to cancel registered trials. Focused checks
covered intact and tampered child artifacts, stage cancellation, and trial
registration and cancellation. Automatic external reconciliation and cleanup
remain open. Resume the implementation at WS-1-003 by binding the original
intake invocation and storing the source retrieval checkpoints.

## Workstream 1 source and container recovery

The original intake invocation binds the issue, explicit page list, criteria,
fixture digest and effective configuration. A resumed intake reuses its verified
source snapshot. Use a new intake to fetch updated source material. CI discovery
checkpoints bind the repository, workflow, commit and attempt to the discovered
run ID. Verifier container identities are journaled before launch and reconciled
by exact label, name and container ID on verifier recovery. Lifecycle checkpoints
also record a monotonic elapsed sample during cooperative waits.

Next resume at WS-1-006. Finish interruption of bounded reads and propagation
through owned subprocess cleanup. The matrix records remaining gaps. For this
pass, four focused tests passed. Comprehensive verification remains pending.

## Workstream 1 cancellation and timing

HTTP response reads cooperate with lifecycle cancellation between chunks.
Verifier cancellation on POSIX terminates its isolated subprocess group.
The journal blocks completion when verifier container cleanup remains
unconfirmed. Remote DEV recovery checks the bound deployment using GET
and persists terminal or uncertain observations. Reports separate observed
agent aggregate duration, wall duration and overlap.

WS-1-016 and WS-1-017 are implemented with focused approval continuity and
stale receipt coverage. Next resume at WS-1-006. Complete cancellation of
blocked network operations and durable owned subprocess reconciliation.
Five targeted tests passed. Comprehensive verification remains pending.

## Basic functional coverage checkpoint

| Workstream | Basic path | Remaining coding |
| --- | --- | --- |
| Lifecycle | Journaled stages, approvals, resume and local recovery | Preserve the eight open WS-1 entries. |
| Trusted CI | Existing protected evaluator, monitoring and signed raw archive ingestion | Independent review evaluation in CI and protected end-to-end target verification. |
| Rubric | Authenticated local and CI observations feed the existing scorer | Collector coverage, review ground truth and additional automated score snapshots. |
| Providers | Existing adapters and shared budgets, including an optional per-requirement run ceiling | Experiment-wide policy binding across trial overrides and extended usage handling. |
| Research | Isolated trials, comparisons and independent human baseline submission | Managed baseline execution evidence collection and automatic terminal measurements. |
| Remote DEV | Broker client/server, pending queue worker, hosted Docker and Kubernetes | Broker restart recovery and deployment-specific verification. |
| Jira | Hosted ingress with optional polling, requirement draft worker and renewal ticks | Worker leases, recovery and direct Jira event authentication remain partial. |

Run `dev experiment-baseline --directory TRIALS --trial ID --measurements SEALED_JSON
--deliverable PATH --config POLICY` to submit human measurements. Seal the payload
under verifier purpose `human-baseline` and bind the experiment, trial, deliverable
manifest digest and evidence. `dev dev-broker-work --store DB --config POLICY`
selects the oldest pending deployment. Hosted Jira enables optional worker ticks
through `enterprise.intake.hosted.drain_enabled` and `poll_enabled`. Renewal uses
the existing `enterprise.intake.webhook.auto_renew` policy. Automatic provider
execution is excluded from the hosted draft worker, which preserves approval gates.

Three targeted CI and broker tests passed. Ruff, compilation and Git whitespace
checks passed. Comprehensive verification remains pending. Resume the basic pass
at WS-2 review evaluation, then connect remaining mandatory handoffs. The
requirement-level statuses remain separate from the existence of a basic path.

## Basic handoff progress

- Trusted CI consumes `review.json` beside the existing target bundle manifest.
  Place independent `review-reference.json` in the protected acceptance parent
  and acceptance tests under its `tests/` child. The reference binds specification,
  manifest and canonical findings digests, and includes defect IDs and labels.
  Signed review evidence feeds defect detection and finding accuracy. Missing
  review labels remain unevaluated; unlabelled findings remain unverified.
- Managed experiment plans bind `effective_configuration_sha256` to canonical
  sorted JSON of the effective configuration. The first executable trial pins
  that digest in SQLite. Development and verification reject trial overrides.
  Trial execution and cancellation write evidence under `execution-evidence/`.
- Hosted Jira can explicitly enable the Requirement Agent through
  `enterprise.intake.hosted.allow_provider`, which defaults to false.

The basic milestone remains open. The next handoff is automated export and
submission of the exact stored review and artifact to protected CI, followed
by conversion of managed terminal evidence into comparative measurements.
Direct Jira event authentication and orchestration across the remaining stage
boundaries also remain open. Three focused tests and static checks passed.
No comprehensive or live verification was performed.

## Essential transfer progress

`dev ci-bundle --store DB --run-id ID --config POLICY --output DIRECTORY`
exports the approved specification and verified artifact. Add `--include-review`
to include the exact stored review after local verification. This creates a new
local directory. Authorized publication and CI dispatch are still separate handoffs.

Experiment reports read managed evidence when an explicit measurement is absent.
Available elapsed time, measured human effort, token usage, estimated provider
cost and receipts are surfaced. Total cost and incomplete measurements remain
unavailable. CSV and Markdown integration remains open.

Direct Jira Cloud admin webhooks use `POST /jira/direct`. Configure
`enterprise.intake.direct.mode: jira_cloud_admin_hmac`, the tenant hostname and a
`secret_env` pointing to a unique per-tenant secret. The route validates
`X-Hub-Signature` before evaluating the tenant and project. The relay route remains
separate. See [Atlassian secure Jira webhooks](https://developer.atlassian.com/cloud/jira/platform/webhooks/).

Next resume at authorized publication of the local bundle, persisted CI dispatch
identity and monitoring handoff. Full verification remains pending.

## Authorized CI and report handoffs

Use `dev ci-submit --store DB --run-id ID --config POLICY --actor VERIFIER
--authorize-submission` for initial CI after approved artifact generation.
After review, repeat with `--include-review`. Enable both
`github.allow_bundle_publication` and `github.allow_dispatch`. Configure a
separate `github.bundle_branch` in a private repository, and allow the protected
workflow ref in `github.dispatch_refs`. Configure `dispatch_ref` and
`dispatch_commit` in `enterprise.delivery_policy.remote_ci`. The commit pins
the evaluator workflow run identity; the verified artifact and optional review
have independent digests. Protect the signing environment and workflow revision.
Never use the bundle publication branch as a dispatch ref.

SQLite records publication and dispatch intents. The dispatch API returns the
exact run ID; monitoring and attestation consume that ID directly. If an
external write outcome is uncertain, reconcile it before another submission.
When the automation policy is configured, review requires initial CI evidence
and delivery also requires independent evidence for the exact approved review.

Use `dev experiment-report --directory TRIALS --report-dir OUTPUT` to export
JSON, CSV and Markdown from one normalized report. The projection includes
available recorded delivery, human, agent, approval, quality, review and provider
measurements. Missing values stay unavailable. Comprehensive and live workflow
verification are deferred. Autonomous sequencing between operator-driven stages
and full comparative evidence coverage remain open.

## Unified operator workflow

Start with `dev workflow-start ISSUE --store DB --config POLICY --repo SOURCE
--out OUTPUT --acceptance-dir TESTS --actor VERIFIER`. Use
`dev workflow-resume --store DB --run-id ID` after an approval or pause. The
existing approval commands still require the human actor and exact digest.
A single resumed invocation drives ordinary stage transitions. The independent
stage commands remain available. Enable provider and remote permissions with
`--allow-provider`, `--authorize-ci` and `--authorize-remote-dev` as applicable.

Unified runs persist a plan, current stage and outcome in SQLite. They reuse
the existing generation, testing, submission, review, correction, validation
and score handlers. CI quality runs before review. Independent CI review runs
against the stored review. Managed trials can be selected with `--experiment`
and `--trial`; reports reuse normalized evidence and identify unavailable inputs.
Human baselines require an independently signed submission. Assisted runs pause
for recorded human coding or testing participation before agent work begins.

Basic orchestration was checked with mocked approval pauses, CI ordering and
DEV handoff. Comprehensive verification remains pending. Next check the real
local verification, blocked correction and post-approval resume paths together
using an authorized disposable fixture. Advanced recovery remains deferred.

## Local review and workflow integration

Configure `enterprise.delivery_policy.local_review.reference_dir` outside the
evaluated workspaces. Store independent references as `MANIFEST_SHA256.json`
with the same specification, manifest, findings digest, defects and labels as
the trusted CI reference. Set `local_review.required` to pause final approval
readiness until applicable local labels can be evaluated. A configured remote
CI requirement continues to use remote evidence exclusively.

The unified workflow now evaluates and signs local review evidence, archives
it on correction and exposes applicable metrics to scoring and reports.
A disposable fixture checked the real CLI and SQLite handlers with mocked
provider and Docker boundaries. It covered both digest-bound approvals, one
bounded correction, reevaluation, post-approval validation, scoring and report
generation. The basic routing is implemented. Live and comprehensive
verification, advanced recovery and exhaustive evidence collection are pending.

## Phase 2 checkpoint

CI evidence preserves observed queue and execution timing. A rerun queue is
unavailable when GitHub supplies only the original creation timestamp. Local
recovery assessments record elapsed time. Lost dispatch responses can be
reconciled by exact correlation, repository, workflow and revision using read
access. Missing or ambiguous results block replay. Hosted and manual intake
drains share a process-owned SQLite lock.

Next resume at WS-1-006. Cancellation during blocked DNS, connection and
header operations still depends on transport behavior. WS-1-007 also needs
durable ownership for abandoned subprocesses. Five focused tests, Ruff,
compilation and whitespace checks passed. Comprehensive verification remains pending.

## Cross-workstream closure checkpoint

Blocked read-only HTTP observations release the workflow on cancellation or
deadline. An in-flight system read can remain active until the transport
returns, so full resource termination remains partial. Abandoned provider
reservations are tied to their exact journal stage and marked uncertain while
retaining their original charges. All Jira draft entry points share one
process-owned worker lock. Authenticated CI acceptance, security and guardrail
results feed existing criteria when available.

Next resume at WS-1-006 and WS-1-007. Complete cancellation of the actual
transport and durable subprocess ownership after supervisor death. Other
mandatory gaps remain in the matrix. Five focused tests, Ruff, compilation
and Git whitespace checks passed. Comprehensive verification remains pending.

## Closure pass ? 26 November 2026

**Implementation remains incomplete. Comprehensive verification remains deferred.**
Branch `implementcode`, HEAD `edd8ce6`. No commit, push, live request, deployment,
workflow dispatch or CP-4 execution was performed. The configured `.env` was not modified.
The inventories below are prior snapshots; this section supersedes their descriptions
of the capabilities changed in this pass. They are not completion certificates.

| Workstream | Implemented in this pass | Implementation status | Verification | Mandatory remaining work |
| --- | --- | --- | --- | --- |
| 1 Lifecycle | Pre-intake run identity; recoverable delivery-to-run association; parent/child journals through parallel generation; shared cancellation; reporting journal | PARTIALLY_IMPLEMENTED | UNIT_TESTED | Cache/reuse completed pre-run source stages; comprehensive child-stage continuation; automatic external resource reconciliation |
| 2 CI | Reusable protected evaluator workflow; isolated Docker evaluation; separate signing job; exact-identity discovery and bounded persistent monitoring; `ci-monitor` connects to existing attestation and delivery gates | PARTIALLY_IMPLEMENTED | MOCK_TESTED | Production-ready target workflow policy validation, full raw-report binding at ingestion, and complete protected review/coverage evaluator coverage |
| 3 Rubric | Original raw evidence files and attachments are hashed; independently signed observations bind run, artifact, rubric and attempt; counts derived by evaluation method; registry exposes source/collection/verification fields | PARTIALLY_IMPLEMENTED | UNIT_TESTED | Direct parsers and automatic collectors for every original measurement; stage-triggered scoring before artifact creation; criterion-level freshness and complete source semantics |
| 4 Budgets | Pre-run requirements use persistent reservations; atomic experiment totals span registered trials/repetitions; provider-specific cache/reasoning fields; estimated cost distinguished from unavailable billing; normalized usage in reports | PARTIALLY_IMPLEMENTED | UNIT_TESTED | Freeze experiment-wide policy at registration and enforce it across heterogeneous trial configurations; complete fallback capability/failure matrix |
| 5 Research | Idempotent trial registration, stage dispatch through existing handlers, cancellation/status; six additional review seeds covering missing categories; independent severity accuracy; 24 synthetic enforcement probes | PARTIALLY_IMPLEMENTED | UNIT_TESTED | Human-baseline deliverable submission and verification path; autonomous trial continuation and terminal measurement collection; source-injection/model-response experiments (probes only test output enforcement) |
| 6 Remote DEV | Authenticated WSGI broker, persistent idempotent requests, allowlisted DEV environments, artifact catalog, signed receipts, Docker harness worker, restricted Kubernetes Job/ConfigMap adapter with ownership and cleanup | PARTIALLY_IMPLEMENTED | MOCK_TESTED | Automatic interrupted-resource reconciliation; structured per-step failure evidence and resilient cleanup recovery; complete interruption/timeout adapter coverage |
| 7 Hosted Jira | Bounded HMAC ingress, health route, loopback server, durable renewal state/history, reconcile-before-refresh, retry intervals, existing polling and dead-letter queue | PARTIALLY_IMPLEMENTED | MOCK_TESTED | Native Jira-to-relay authentication deployment path and managed renewal/poll/drain service integration; current scheduler is an operator-invoked tick |

### Changes and executable commands

Extended existing `lifecycle.py`, `specifications.py`, `cli.py`, `development.py`,
`agents/__init__.py`, `provider_budget.py`, `llm.py`, `delivery_evidence.py`,
`verification.py`, `github_evidence.py`, `remote_dev.py`, `experiments.py`,
`scenarios.py`, `intake.py`, and `sdlc.yaml`. New files in this pass:
`.github/workflows/trusted-evaluator.yml` and `tests/test_implementation_closure.py`.
Previous uncommitted files remain preserved.

Additional SQLite tables are created additively in the existing store:
`experiment_runs`, `provider_usage`, `ci_monitors`, `dev_deployments`,
`webhook_schedule`, and `webhook_history`. No new database service is required.
Existing per-run SQLite locks continue to serialize operations.

```powershell
.\.venv\Scripts\python.exe -m agentic_sdlc dev ci-monitor --help
.\.venv\Scripts\python.exe -m agentic_sdlc dev experiment-schedule --directory TRIALS --store research.db
.\.venv\Scripts\python.exe -m agentic_sdlc dev experiment-trial --help
.\.venv\Scripts\python.exe -m agentic_sdlc dev intake-serve --store intake.db --config hosted.yaml
.\.venv\Scripts\python.exe -m agentic_sdlc dev intake-renew --store intake.db --config hosted.yaml
.\.venv\Scripts\python.exe -m agentic_sdlc dev dev-broker-serve --store broker.db --config broker.yaml
.\.venv\Scripts\python.exe -m agentic_sdlc dev dev-broker-work --store broker.db --config broker.yaml --deployment DEPLOYMENT_SHA256
```

`experiment-trial` supports run/resume/correct/testing/review/validate/report/status/cancel.
It preserves normal approval dependencies. HUMAN_BASELINE cannot invoke agent generation
or AI review. Baseline submission remains an explicit coding gap, not simulated human work.
Registered trials in one store share `enterprise.budgets.experiment`. Pending/unknown
provider usage retains reservations. Actual billed cost is null when unavailable.

`criterion-attest` now requires `raw_evidence` references `{path, sha256}` relative
to the evidence document. Each source contains `actor`, `payload`, and `mac` sealed
with the existing verifier authority purpose `criterion-source`. Payloads bind
`run_id`, `manifest_sha256`, `spec_sha256`, `attempt_id`, `rubric_version`,
`criterion_id`, `method`, `source`, `attachments`, and distinct `observations`.
Human observations require verdict and rationale; baseline observations require
finite measured baseline/actual values and equivalent task identity. Other observations
record expected/observed values. This preserves evaluator provenance but does not yet
independently parse every attachment format to establish observation semantics.

### Operator configuration for the testing/deployment phase

Hosted ingress requires `enterprise.intake.hosted.enabled: true`; it binds loopback
on port 8787 behind an operator-owned TLS reverse proxy. `/jira` accepts the existing
HMAC relay envelope, not unauthenticated native Jira deliveries; `/health` is read-only.
Use a process supervisor with ingress request timeouts and concurrency limits.
Renewal needs `webhook.auto_renew`, `allow_write`, OAuth credentials and `managed_ids`.
An OS scheduler can invoke `intake-renew` periodically; state prevents early repeat
refresh. Missing/expired registrations are reported, never silently recreated.
Run existing polling/drain commands separately with their own explicit policy scopes.

The DEV broker is disabled by default. Server configuration lives at
`enterprise.remote_dev.server`: `enabled`, `port`, `token_env`, `receipt_key_env`,
`environments`, and `artifacts` (manifest digest to operator-provisioned bundle path).
Each environment selects `docker` or `kubernetes` and optional trusted `configuration`
overrides. The DEV command must invoke `/opt/capstone/dev_harness.py` with nonempty
build, start, health and smoke operations. Artifacts are provisioned out-of-band;
requests cannot provide arbitrary host paths or commands. Set client
`enterprise.remote_dev.receipt_key_env` to verify the broker MAC as well as HTTPS.

Kubernetes environments additionally specify `context`, `namespace` beginning `dev-`,
`kubeconfig` and an immutable harness `image` digest. Grant only namespace-scoped Job,
ConfigMap and network-policy read permissions. Application pods have no service-account
token, run nonroot with dropped capabilities, and mount artifacts read-only. The adapter
requires a single deny-all ingress/egress NetworkPolicy; the cluster must enforce it.
Inline artifact data is limited to 500 KB. Oversized artifacts fail explicitly.
The existing harness builds application code inside the isolated runtime; it does not
build/push arbitrary application images. Interrupted remote execution fails closed for
operator reconciliation; automatic recovery is not yet complete.

Call the reusable CI workflow from a protected target workflow pinned to a reviewed
40-character evaluator revision. Configure protected repository/environment variables:
`CAPSTONE_EVALUATOR_REPOSITORY`, `CAPSTONE_EVALUATOR_REVISION`,
`CAPSTONE_EVALUATOR_POLICY`, `CAPSTONE_ACCEPTANCE_DIRECTORY`, `CAPSTONE_CALLER_WORKFLOW`.
The protected `capstone-trusted-evaluator` environment supplies
`CAPSTONE_CI_EVALUATOR_KEY` only to the fresh attestation job. Protect workflows,
evaluator revisions, acceptance tests and environment administration with repository
rules. The target bundle contains `manifest.json`, `integrated/` and the approved
`specification.json`; input digests must match stored approvals. Evaluation has no
signing secret; target code runs only inside the network-disabled Docker verifier.
Configure required archive names to match the workflow's attempt-specific artifacts.
No workflow was dispatched during this pass.

### Verification and remaining closure accounting

49 focused tests passed across closure, final coding, actual-verification mocks,
enterprise coding, development, requirements and GitHub evidence. The closure file
contains 16 tests. Ruff and compilation passed before the final documentation edits;
Git whitespace checking passed. No full regression, Docker/Kubernetes deployment,
hosted request, paid model call, or research trial ran. The system Python lacked
`python-dotenv`; checks used the existing `.venv` interpreter successfully.

Seven closure workstreams remain PARTIALLY_IMPLEMENTED. The original attached
53-section brief was located and its section anchors reviewed. A complete, audited
per-clause inventory has not yet been produced; a complete requirement count or
implementation percentage would therefore be unsupported. Existing W01?W17 and
F01?F08 inventories remain historical mappings, not substitutes for that audit.
Do not declare CODING COMPLETE from these test counts.

After coding closure, the comprehensive local testing commands are:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q src mcp setup_env.py
git diff --check
```

Live testing later needs model configuration, target/base revision, authenticated
human/verifier identities, Actions read/dispatch permission and protected CI signing
configuration, Jira OAuth/administrator permissions, authenticated relay hosting,
Confluence write permission only for publishing, and authorized DEV infrastructure.
Measured human baselines remain future experiments. Additional clouds/providers,
dashboards and billing integrations are FUTURE_ENHANCEMENT, not closure requirements.


## Final coding pass inventory

These rows supplement the workstream inventory below. They distinguish executable paths from unresolved implementation work. No remote actions or comprehensive verification ran in this pass.

| ID / original sections | Implementation and location | Implementation status | Tests and verification | Remaining gap |
| --- | --- | --- | --- | --- |
| F01 / 12?17, 23?25, 28?30 | `lifecycle.py`, `specifications.py`, `cli.py` serialize stored-run CLI stages, journal timestamps, detect stale claims, and enforce cancellation on receipt creation. Docker checks stop and clean up on cancellation. | PARTIALLY_IMPLEMENTED | `test_final_coding.py`, `test_actual_verification.py` / UNIT_TESTED | Intake and requirement generation before run creation, child-stage journals, experiment scheduling and mid-request hosted cancellation are outside the unified stage contract. Existing manual external commands still require independent reconciliation. |
| F02 / 16?22, 25 | `delivery_evidence.py`, `github_evidence.py`, `verification.py`: `ci-attest` fetches exact run/attempt metadata, checks workflow/jobs/freshness, downloads and hashes required archives, verifies a separate evaluator signature, then seals a workspace-bound receipt. A required CI policy gates validation and scoring. | PARTIALLY_IMPLEMENTED | `test_final_coding.py`, `test_github_evidence.py` / MOCK_TESTED | No bundled trusted workflow produces this signed artifact for arbitrary evaluated repositories. Operator must supply a protected evaluator workflow. Artifact upload and remote CI monitoring remain open. |
| F03 / 31?35 | `criterion-registry` describes all default rubric criteria and methods. `criterion-attest` imports independently measured counts. `score` checks attestation identity and adds criterion attempt/digest metadata. Direct observations preserve prior provisional scores. | PARTIALLY_IMPLEMENTED | `test_final_coding.py`, `test_actual_verification.py` / UNIT_TESTED | Verified collection adapters for every criterion are still missing. Imported source digests are evaluator statements, and the CLI does not fetch and verify each raw source object. Independent findings, verdicts and baselines are still required. |
| F04 / 10, 28?29, 40?41 | `provider_budget.py`, `llm.py`: transactionally reserve request, agent, stage and run budgets, retain uncertain spend, reconcile measured usage, and record retryable failover. Generation threads share the same run ledger. | PARTIALLY_IMPLEMENTED | `test_final_coding.py`, existing provider fixtures / UNIT_TESTED | Experiment-wide caps, requirement generation before a stored run, cached/billed-token breakdown, and provider-specific exact token bounds remain open. Estimate-based admission cannot guarantee exact provider billing. |
| F05 / 34?39, 45?47 | `scenarios.py`: eight runnable coding task seeds with private acceptance files; defect labels for four defective starts and clean fixtures; eight labelled write-preflight guardrail probes; existing three-mode snapshots and measured comparison. | PARTIALLY_IMPLEMENTED | `test_final_coding.py`, `test_enterprise_coding.py` / UNIT_TESTED | Remaining defect categories and guardrail scenarios, managed trial execution/cancellation and provider comparison across experiments are open. Coverage seed currently checks for generated tests, not measured branch coverage. |
| F06 / 24?25 | `remote_dev.py` is an opt-in HTTPS DEV broker client wired into signed post-approval validation. It scopes deployment identities and resumes a persisted intent through GET. Mandatory evidence includes cleanup. | PARTIALLY_IMPLEMENTED | `test_final_coding.py` / MOCK_TESTED | A broker implementation and direct remote container/Kubernetes or standalone local-process adapters are absent. The broker must enforce its own DEV environment policy and resource ownership. |
| F07 / 26 | `intake.py`, `specifications.py`, `cli.py`: OAuth webhook registration/listing/renewal/removal, reconciliation journals, HMAC relay WSGI entry point, polling and durable dead letters. | PARTIALLY_IMPLEMENTED | `test_final_coding.py`, `test_enterprise_coding.py` / MOCK_TESTED | Public TLS hosting, independently validated Jira-to-relay ingress, and automatic renewal scheduling are deployment responsibilities. No dynamic webhook was created. |
| F08 / 8, 28?30, 36?39, 42 | Existing pipeline and reports consume the new stored stage journal, provider ledger, evidence gates and DEV results. | PARTIALLY_IMPLEMENTED | Focused lifecycle and scoring tests / UNIT_TESTED | Fully automated cross-component resume, external reconciliation workflow and complete comparison exports remain open. |

### New policies and commands

- `enterprise.delivery_policy.remote_ci` takes `required`, `repository`, workflow filename, required `jobs`, archive names in `artifacts`, `signed_archive`, required evaluator `checks`, `evaluator_revision`, `max_age_seconds`, and `evaluator_key_env`. Use `dev ci-attest RUN_ISSUE --store DB --config POLICY --actor VERIFIER --evidence CI_JSON`. The required archive contains a bounded `attestation.json` with a canonical JSON payload and HMAC. Provision the evaluator key only to the protected evaluator and trusted verifier, never to evaluated code. The attestation binds the repository, commit, workflow, run and attempt, specification and artifact digests, evaluator revision and check results.
- `dev criterion-registry` lists scoring requirements. `dev criterion-attest RUN_ISSUE --store DB --config POLICY --actor VERIFIER --evidence COUNTS_JSON` accepts an artifact digest, rubric version, method, measured and passing counts, reason and source digests. A criterion with missing evidence remains unevaluated.
- `enterprise.budgets` accepts scopes `request`, `run`, `stages`, and `agents`. Limits include `max_input_tokens`, `max_output_tokens`, `max_total_tokens`, and `max_cost_usd`. Configure `llm.max_tokens`; a cost cap additionally requires USD rates. `llm.fallback` lists at most three explicit compatible real providers and is tried only for rate limits, timeouts, and selected service failures. Interrupted and unmetered requests retain their reservations.
- `enterprise.remote_dev` defaults to disabled. An authorized DEV broker supports `PUT` (create by deterministic identity), `GET` (recover or monitor), and `DELETE` (cancel) at `/deployments/{id}`. `dev validate --authorize-remote-dev` permits a new broker operation only under an enabled, required policy after approval and local verification. Lost or ambiguous creation responses require reconciliation by GET before resetting anything.
- `dev jira-webhook list|register|renew|remove` uses OAuth 2.0. Mutations need `--authorize-write`, a newly named `--journal`, and enabled policy; renew/remove also need an allowlisted `--hook-id`. Register the relay callback, have that relay verify Atlassian's delivery, and HMAC-sign the exact bytes forwarded to the WSGI consumer. For administrator-managed integrations, configure a Jira Cloud webhook through administration and retain the HMAC relay and polling fallback. [Jira webhook API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/)
- Use `dev experiment-seed --scenario bug-fix --directory TRIALS --evaluator TRUSTED` to create paired workspaces with acceptance and ground truth outside them. `dev experiment-guardrails --directory DISPOSABLE` runs synthetic write-policy probes without applying changes. These outputs are fixtures, not measured productivity evidence.

## Checklist A ? Implementation completion

The workstream IDs cover the revised instruction and map to the original brief sections. Project and process requirements in ??1?7, 43?44, and 47?53 are tracked in the checkpoint, verification checklist, and research documentation.

| ID | Brief sections | Status | Code and remaining implementation |
| --- | --- | --- | --- |
| W01 Agents | 8?11, 16, 19, 23?25 | PARTIALLY_IMPLEMENTED | Requirement, independent/parallel code and test generation, trusted Docker execution, review and post-approval DEV checks are executable through `cli.py`, `development.py`, and `verification.py`. Per-agent routing is supported. Remaining: richer quality and coverage traceability. |
| W02 CLI | 11?14, 28?30, 42 | PARTIALLY_IMPLEMENTED | Stored issue selectors, exact-digest approval stages, `code`, `testcase`, `correct`, active activity and experiment commands. Independent drafts are staged outputs. Issue-scoped metrics and comparison aliasing, and digest-bound review rejection are available. Remaining: fully integrated pipeline resume commands. |
| W03 Orchestration | 8, 12?14, 25, 28 | PARTIALLY_IMPLEMENTED | `SpecificationStore` preserves run and attempt state, serializes generation, prevents stale receipts and retains historical evidence. Remaining: unified recovery and exclusive claims for testing/review/validation and full lifecycle cancellation. |
| W04 Correction | 23, 29?30, 33 | PARTIALLY_IMPLEMENTED | `dev correct` uses authenticated failed checks or blocking findings, regenerates from the verified prior workspace under the original requirement approval, bounds cycles, and archives prior receipts. Must rerun checks and review. Remaining: more precise fault categories and selectively rerunning checks, retained test attribution, and stronger targeted editing. |
| W05 Guardrails | 15, 18?19, 21 | PARTIALLY_IMPLEMENTED | Protected files, dependency manifests, credential paths, read-only Docker mounts, no container network, and structured blocked-write observations. Remaining: independently verified runtime controls, per-run/cumulative token and cost budgets, and guardrail latency. |
| W06 Verification | 16?22, 25 | PARTIALLY_IMPLEMENTED | Trusted receipts; Actions jobs; commit/attempt evidence and bounded archive digest validation. Remaining: real target-specific protected workflow design, mandatory CI claim binding, coverage and criterion-level quality evidence. |
| W07 Review | 23, 35, 39, 46 | PARTIALLY_IMPLEMENTED | `verification.py` derives stable finding IDs and provenance and deduplicates findings; historical attempts remain available. `metrics.py` computes precision, recall and F1 from independent labels. Exact-digest human adjudication persists with signed labels. Review reports show repeated findings and those not observed on the next attempt. Remaining: independent resolution confirmation and richer remediation recommendations. |
| W08 Human effort | 29?30, 36 | PARTIALLY_IMPLEMENTED | Active review/rework and other activity kinds persist with UTC timing, recovery by interruption, and per-actor exclusivity. Reports separate waiting time. Remaining: independently attested interventions/diffs and human cost calculation. |
| W09 DEV validation | 24?25 | PARTIALLY_IMPLEMENTED | `dev validate` performs a fresh isolated DEV command after exact review approval, with sealed evidence and gated scoring. The trusted image includes `dev_harness.py` for bounded build commands, service startup, loopback health/API smoke probes, and cleanup. Docker captures output digests. Remaining: richer log evidence and remote DEV adapters. |
| W10 Scoring | 31?33 | PARTIALLY_IMPLEMENTED | Existing 100-point engine, actual first/final snapshots, missing gates, and prior-attempt histories. Remaining: independent evidence adapters for all rubric criteria and automatic score deltas. |
| W11 Guardrail metrics | 34, 39, 47 | PARTIALLY_IMPLEMENTED | Existing independent labels and rates; runtime blocked-write audit events record policy and severity. The metrics command joins supplied independent labels and reports policy rates and severity counts. Remaining: policy-level latency and escaped-event collection. |
| W12 Productivity | 29?30, 36?37 | PARTIALLY_IMPLEMENTED | Active effort records, stage timestamps, provider usage, first/final scores, and measured comparison/OPI formulas. Experiment reports calculate outcome rates, validated tasks per measured human hour and cost per validated task when data is sufficient. Remaining: complete delivery, CI queue and infrastructure/human cost collection. |
| W13 Automation | 22, 26?27 | PARTIALLY_IMPLEMENTED | Authenticated Jira relay envelopes, deduplication, durable queue and deduplicated draft persistence; Confluence discovery; explicitly gated GitHub dispatch and archive retrieval. Explicit polling and queue drain are available. Policy-scoped Jira transitions and Confluence page publishing are available. Remaining: hosted HTTP receiver and webhook registration automation. |
| W14 Providers/MCP | 10, 40?41 | PARTIALLY_IMPLEMENTED | Existing compatible adapter, native Anthropic and Gemini-compatible adapters, exact hosted destination allowlist, request bounds, attribution and configurable token pricing. Per-agent settings are supported under `enterprise.agent_models`. Remaining: safe real-provider failover and retries, cumulative cost policies, and additional MCP routing. |
| W15 Experiments | 38?39, 46?47 | PARTIALLY_IMPLEMENTED | `experiments.py` creates equal isolated workspaces for all three modes, persists terminal measurements, and compares validated paired trials. Remaining: independently attested seeded-defect/guardrail scenarios and carry-through of source revision/acceptance evidence. |
| W16 Reporting | 28?30, 42, 52 | PARTIALLY_IMPLEMENTED | `dev report --report-dir` exports JSON, CSV attempt timeline and Markdown with explicit missing scores; activity and attempt history included. Remaining: full research rollups and comparative reports in every format. |
| W17 Config and docs | 41, 48, 50 | PARTIALLY_IMPLEMENTED | Packaged correction, dependency, approved-host, relay and dispatch policies; `.env` sample and commands below. Remaining: a validated complete policy schema and operator setup for unfinished adapters. |

## Checklist B ? Testing and verification

| Scope | Status | Recorded evidence and next phase |
| --- | --- | --- |
| Jira, Confluence, GitHub historical CI | LIVE_VERIFIED | Prior checkpoint below. No live calls were repeated in the coding-first phase. |
| Existing Docker fixtures | ACTUAL_LOCAL_VERIFIED | Prior checkpoint below. This phase changed post-approval execution and requires later Docker regression. |
| Human effort, attempt archives, relay deduplication, experiment snapshots, validation/scoring, requirements | UNIT_TESTED | 45 focused tests passed across enterprise coding, actual verification adapters, generation, requirements, Confluence, GitHub evidence, scoring and guardrails. Ruff, compilation and Git whitespace checks passed. The full regression result below predates these changes. |
| Configured provider adapters and external integration writes | MOCK_TESTED | Focused mocked provider and Jira scope checks. GitHub artifact, Confluence publishing and actual hosted paths still need verification. No hosted calls, dispatch, deployment, or external writes performed. |
| CP-4 and actual model execution | AUTHENTICATION_REQUIRED | Approved model configuration, target repository/base revision, grounded criteria, relevant context where available, and explicit human decisions are deferred. CP-4 remains blocked. |
| Remote workflows and remote DEV | PERMISSION_REQUIRED | Explicit authorization needed before pushing or dispatching workflows, deploying, or making other external changes. No human baseline runs performed. |

### Local commands added in the coding-first phase

```text
python -m agentic_sdlc dev CP-4 --store workflow.db
python -m agentic_sdlc dev code CP-4 --store workflow.db --config policy.yaml --repo SOURCE --out OUTPUT
python -m agentic_sdlc dev testcase CP-4 --store workflow.db --config policy.yaml --repo SOURCE --out OUTPUT
python -m agentic_sdlc dev correct CP-4 --store workflow.db --config policy.yaml --repo SOURCE --out OUTPUT --allow-provider
python -m agentic_sdlc dev review-start CP-4 --store workflow.db --actor reviewer --config policy.yaml
python -m agentic_sdlc dev review-end CP-4 --store workflow.db --actor reviewer --config policy.yaml
python -m agentic_sdlc dev report CP-4 --store workflow.db --report-dir REPORTS
python -m agentic_sdlc dev experiment-prepare --plan plan.json --repo SOURCE --directory TRIALS
python -m agentic_sdlc dev experiment-record --directory TRIALS --trial 01-human_baseline --measurements measured.json
python -m agentic_sdlc dev experiment-report --directory TRIALS
```

`dev intake-receive` accepts a trusted relay payload and authenticates its hexadecimal HMAC-SHA256 over `DELIVERY_ID.UNIX_TIMESTAMP.` followed by the exact payload bytes. Configure the relay key through `CAPSTONE_JIRA_RELAY_KEY` and allow project keys in `enterprise.intake.projects`. `dev intake-queue` lists pending deliveries. `dev intake-draft --delivery-id ID` fetches and persists the Jira specification using the existing intake path. Set `enterprise.intake.allow_poll` and run `dev intake-poll` for a scoped backlog scan, and `dev intake-drain --limit N` to process pending items.

For local service validation with a rebuilt trusted verifier image, set `enterprise.container.commands.dev` to the interpreter path, `-I`, `/opt/capstone/dev_harness.py`, and a single JSON argument. The plan accepts `build` (argument vectors), `start` (one argument vector), `health` (a loopback URL object), `smoke` (additional URL objects), and `timeout` in seconds. Probe objects accept `url`, `status`, and an optional `contains` body assertion. Builds must use writable temporary paths, and the process must bind to the container's loopback. The verifier preflight and post-approval DEV check must use the same pinned image and command.

For hosted providers, use a trusted config with `llm.provider` (`openai`, `anthropic`, or `gemini`), `llm.model`, `llm.base_url`, `llm.api_key_env`, `llm.max_tokens`, and an exact `enterprise.allowed_model_hosts` entry. `enterprise.agent_models` can override provider configuration for `requirement`, `code`, `testcase`, and `review`. Configure both token price rates to compute estimated usage costs. Provider execution requires `--allow-provider`.

For the testing phase, run `python -m pytest -q`, `python -m ruff check .`, `python -m compileall -q src`, and `git diff --check`. For actual Docker regression, use the pinned local verifier image and `examples/enterprise/demo-actual.ps1 -Image IMAGE_ID -FixtureApprovals`, then repeat with `-FailAcceptance`. Use real named approvals and target-specific check commands for the CP-4 execution. External writes require explicit authorization flags and disabled-by-default policy scopes. Run `dev jira-transition --help`, `dev confluence-publish --help`, or `dev ci-dispatch --help` for the respective executable interfaces.

## Current checkpoint on `implementcode`


### Live integration and CP-4 checkpoint

| Requirement | Status | Implementation and tests | Verification and remaining input |
| --- | --- | --- | --- |
| Confluence space, page, keyword, and Jira-linked discovery | LIVE_READ_VERIFIED | `confluence.py`, `jira.py`, `dev context`; `tests/test_confluence_discovery.py` covers pagination, credential destination checks, and linked-page intake | Live account could see 6 spaces and 14 pages. One scoped listing returned 3 pages. Validated a page version and SHA-256; `coding standards` returned 0 matches. CP-4 had no matching links, issue-key results, or title results. |
| Jira backlog triage | LIVE_READ_VERIFIED | `dev backlog --summary` reports issue type, priority, criteria and linked-page counts | Four CP issues were returned. All lacked explicit acceptance criteria and direct Confluence links. The operator confirmed CP-4 only. |
| CP-4 Requirement Agent | WAITING_FOR_USER | `agents/requirements.py` validates quoted criterion suggestions; `dev spec` saves source revision, provenance, traceability and timings. `--criteria-file` accepts human-supplied criteria when Jira has none. | Live source intake persisted run `008dd8a93ea147ec97ece8a9cc364b1f` as `BLOCKED` in `.agentic_sdlc/enterprise-intake/cp4.db`. Await an approved configured loopback model and any relevant Confluence page, then require an actual human requirement decision. Intake digest `6f0da38db399df600e9edfabebded49233e6064a1b76fb72a1088ca7400ccbd9`. |
| Regression isolation | IMPLEMENTED | `tests/conftest.py` uses a per-test env path, strips integration variables and blocks non-loopback connections in the pytest process. MCP test servers inherit the isolated path with synthetic inputs. | `.env` is Git-ignored and unchanged by verification. Full isolated regression passed (150 passed, 1 skipped) without accessing the real file. |
| Independent CI jobs and Actions evidence | LIVE_READ_VERIFIED | `.github/workflows/ci.yml`, `.github/workflows/enterprise-verification.yml`, `dev ci-evidence`, `tests/test_github_evidence.py` | Historical workflow syntax and graph validated locally. Live historical CI run `45537258564`, attempt 1, succeeded with three jobs and no artifacts. Ingestion binds run metadata and jobs to an exact commit and attempt. Artifact metadata is run-scoped and archive bytes are unverified. Remote dispatch, protected target workflow setup, and independently attested CI-to-delivery binding remain open. |
| Actual downstream CP-4 execution | WAITING_FOR_USER | Existing `development.py`, `container_verifier.py`, `verification.py`, and lifecycle `dev score` | Target repository, base revision, trusted verifier setup and human approvals are needed. A real target build/startup/health test after final approval and bounded review correction are outstanding. |
| Research measurements and reporting | IN_PROGRESS | Existing scoring, metrics, and baseline comparison paths. `dev testing` and `dev validate` automatically preserve separate first-pass and final score reports for authenticated actual verification. | No CP-4 verification, quality score, measured human baseline or productivity result exists. Correction evidence and active reviewer instrumentation remain open. |

Run discovery from the repository root using the setup-generated `.env`:

```powershell
python -m agentic_sdlc dev context --spaces
python -m agentic_sdlc dev context --space SPACE_KEY
python -m agentic_sdlc dev context --search 'coding standards'
python -m agentic_sdlc dev context CP-4
python -m agentic_sdlc dev backlog --summary
```

`dev ci-evidence --repository OWNER/REPO --run-id ID --attempt NUMBER
--commit FULL_SHA --workflow ci.yml --output evidence.json` reads an existing
GitHub Actions execution with `GITHUB_TOKEN` and Actions read permission.
It preserves failed conclusions and keeps run-scoped artifact metadata
separate from attempt-scoped job results. It does not dispatch a run or
satisfy a trusted local acceptance, approval or delivery gate.


The live Jira backlog command read the setup-generated `.env` without a YAML
override and returned four CP To Do issues. Confluence discovery and a versioned page read also passed. CP-4 had no linked or matching Confluence page.

This continuation started with a clean tree at `edd8ce6`, tracking
`origin/implementcode`. Baseline regression was 142 passed and 1 skipped.
The rows below supersede older status notes where they overlap.

Regression after the requirement-agent change was 144 passed and 1 skipped.
The Docker success fixture passed all seven checks and reached
`LOCAL_DEV_VALIDATED`. Its score was 7/100 with `PROVISIONAL` coverage
and `PENDING` eligibility. The negative fixture failed only acceptance and
scored 7/100 with `BLOCKED` eligibility. These runs used fixture approvals.

| Brief section | Status | Implementation and evidence | Remaining work |
| --- | --- | --- | --- |
| 9–11, requirement intake and agent execution | MOCK_INTEGRATION_TESTED | `dev spec --allow-provider` calls `agents/requirements.py` using the existing loopback adapter. `tests/test_requirement_agent.py` exercises draft persistence and mandatory-gap blocking. Jira criteria and their identifiers stay authoritative. | Verify a configured live model. Independent coding and testcase entry points, usage budgets across providers, and provider cost accounting are open. |
| 12–14, approvals, recovery, and parallel generation | UNIT_TESTED | `approval.py`, `specifications.py`, `development.py` and `tests/test_development.py`. The local demo prompts for exact requirement and review digests by default. | Real approval for a selected live run, enterprise identity, and full-stage recovery. |
| 15–19 and 24–25, trusted checks and delivery | ACTUAL_LOCAL_VERIFIED | `DockerVerifier` executed the seven configured checks on a deterministic fixture with a deliberately failing independent acceptance test. The verifier and scorer returned `BLOCKED`, with only acceptance failing on the corrected run. | The local DEV check is an import and smoke assertion. Application startup and health checks after final approval, and broader guardrail enforcement, remain open. |
| 20–23, independent CI and correction | IN_PROGRESS | `.github/workflows/ci.yml` has lint, tests, and security jobs. `verification.py` binds reviewed artifacts to verifier receipts. | Trusted CI workflow and artifact ingestion, remote verification authorization, structured findings and bounded correction. |
| 26–27 and 43, Atlassian live reads | WAITING_FOR_USER | `jira.py` and `confluence.py` implement backlog and explicit page reads. Requested `JIRA_USER`, `JIRA_TOKEN`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, and a page ID. | Await read credentials and page selection. Automatic knowledge retrieval and Jira event processing remain open. |
| 28–39 and 42, evidence and research | IN_PROGRESS | Existing lifecycle scoring and independent assessment, baseline comparison and metrics commands remain in place. The Docker failure produced a provisional score of 7/100 with blocked eligibility. | First-pass and final records, active review measurement, complete timing and experiment support, and measured baselines. |
| 44–46, demonstrations and acceptance | IN_PROGRESS | `demo.ps1` exercises the synthetic path. `demo-actual.ps1` can execute Docker checks with interactive digest approval or explicit `-FixtureApprovals` for deterministic verification. | Actual-model and enterprise demos require an approved model, Jira issue, page, and human decisions. |

To reproduce the actual local Docker fixture using an existing trusted image:

```powershell
$image = docker image inspect capstone-verifier-local:latest --format '{{.Id}}'
powershell -NoProfile -File examples/enterprise/demo-actual.ps1 -Image $image
powershell -NoProfile -File examples/enterprise/demo-actual.ps1 -Image $image -FailAcceptance
```

Both commands ask for approval of the exact fixture requirement digest.
The success path also asks for review approval. `-FixtureApprovals`
is for automated runs and records that fixture approvals were used.
The verifier runs generated and existing tests, independent acceptance,
Ruff, Bandit, a Definition of Done check, and a calculator smoke check.
Bandit runs all rules against production and skips only the `assert`
rule for tests, where assertions are expected.

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
| Integrated artifact and quality approval pauses | IMPLEMENTED_UNVALIDATED | `workflow-start` / `workflow-resume` now pause for digest-bound approval of the integrated code/test manifest and then the independent verification result. Quality approval is required before review; both decisions are archived when correction replaces the artifact. CLI approval supports development and quality stages; `dev report` includes these receipts. | This increment has not yet been validated by tests. Local HMAC identities are not enterprise identity or remote attestation. |
| Pause, resume, attempts, and cancellation | INTEGRATION_TESTED | `specifications.py` holds a per-run SQLite lock, persists attempts, records interrupted attempts, bounds retries and supports cooperative cancellation. Recovery and concurrent-run tests in `tests/test_development.py`. | Scope is development staging. Cancellation blocks publication of staged evidence; work already in progress can finish. No automatic replay of real external writes. |
| Parallel isolated code and test generation | OFFLINE_E2E_VERIFIED | `development.py` calls the existing agents with separate copies of the same bounded source snapshot. Barrier test verifies actual concurrency and isolated writes. Merged files are checked against a SHA-256 manifest on resume. | Internal stub only. Use a trusted source directory, not a hostile filesystem. This is file-level isolation, not an OS sandbox for real generated code. |
| Independent verification, review, and local validation | OFFLINE_E2E_VERIFIED | `verification.py` binds a separate verifier's attested checks to the exact integrated manifest, runs the existing static/stub review, enforces quality and final review approvals, and records `OFFLINE_VALIDATED`. A failed independent check blocks review in acceptance tests. | The CLI imports attested outcomes and does not run test commands in the agent process. Trusted independent execution, real DEV validation, coverage collection, GitHub Actions ingestion, and correction cycles remain open. The new quality pause is not yet validated. |
| Stage CLI and lifecycle reporting | INTEGRATION_TESTED | `dev run`, `resume`, `cancel`, `verify`, `review`, `validate`, `report`, and digest-bound decision support. `dev report` returns historical receipts, attempt durations, and approval waiting time. | This increment adds development/quality pauses and report evidence but has not yet been tested. Testing in a sandbox, active human review sessions, and live provider usage remain bounded by existing implementation limits. |
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

For live Jira and Confluence reads, run `setup_env.bat` and select both
integrations. From the repository root, `dev backlog`, `dev spec ISSUE`,
and `dev context PAGE_ID` load the resulting `.env` directly. Older setup
files need `JIRA_JQL` for backlog search. The existing `--config` option
supports fixture and policy overrides. These settings can refer to
credential variable names. Their values belong in `.env` or the process
environment.

Use `python -m agentic_sdlc dev context 12345` for provenance from one
authorized Confluence page. An optional `--output` saves the bounded storage body to a
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
