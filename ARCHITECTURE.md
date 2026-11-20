# Enterprise Agentic SDLC Architecture: MCP GitHub & Jira Integration

## 1. Executive Summary & Overview

This document specifies the architecture of the autonomous **Agentic Software Development Life Cycle (SDLC)** framework, integrating the **Model Context Protocol (MCP)** with enterprise systems—specifically **Jira** for issue/task lifecycle management and **GitHub** for version control, code review, and CI/CD automation.

The architecture addresses the critical challenges identified in enterprise AI coding:
- **Guardrails**: Ensuring automated code changes strictly conform to repository security policies, sensitive path restrictions, and credential protection.
- **Review Load**: Minimizing cognitive and operational overhead on human reviewers through structured diffs, clear traceability, and automated pre-checks.
- **Real Productivity**: Delivering end-to-end task completion (from Jira ticket ingestion to tested GitHub PR) with bounded feedback loops and measurable telemetry.

---

## 2. High-Level SDLC Architecture Workflow

```
+---------------------------------------------------------------------------------+
|                               JIRA (MCP Server)                                 |
|  - Ingest requirement/issue (Backlog / Sprint / Bug)                            |
|  - Update issue progress & comments                                             |
|  - Transition ticket to In Review / Done                                        |
+----------------------------------------+----------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                       AGENTIC ORCHESTRATOR & CONTEXT ENGINE                     |
|  - MCP Client connects to Jira & GitHub servers                                 |
|  - Context Retrieval: RAG / Vector KB + Repository Structure Inspection         |
|  - Task Decomposition & Execution Plan                                          |
+----------------------------------------+----------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                        IMPLEMENTATION & GUARDRAIL GATES                         |
|  - Coder Agent: Generates target changes (Spring Boot, Python, etc.)            |
|  - Tester Agent: Creates/updates unit & integration test suites                 |
|  - Enterprise Guardrail Checks:                                                 |
|      * Secret & credential scanning                                             |
|      * Restricted path & branch protection enforcement                          |
|      * Dependency license & security policies                                   |
+----------------------------------------+----------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                              GITHUB (MCP Server)                                |
|  - Create feature branch (`feat/*`, `fix/*`)                                   |
|  - Commit atomic changes with verified signatures                               |
|  - Open Pull Request referencing Jira Ticket ID                                 |
|  - Trigger CI/CD workflows (.github/workflows)                                  |
+----------------------------------------+----------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                         FEEDBACK LOOPS & REVIEW GATES                           |
|  - CI/CD & Automated Testing (Test failures trigger self-healing loops)         |
|  - Static Analysis & Linting (Ruff / Checkstyle / Bandit)                       |
|  - Human-in-the-Loop (HITL) Code Review & Approval                             |
|  - Merge & Jira Ticket Closeout                                                 |
+---------------------------------------------------------------------------------+
```

---

## 3. Model Context Protocol (MCP) Integration

The **Model Context Protocol (MCP)** standardizes how autonomous agents discover and invoke enterprise tools without vendor lock-in or custom bespoke integrations.

### 3.1 MCP Jira Server Capabilities
The Jira MCP server provides structured primitives for requirement tracking:
- `jira_get_issue(issue_key)`: Retrieves summary, description, acceptance criteria, priority, and reporter.
- `jira_search_issues(jql)`: Discovers assigned tasks, linked epics, and related regression tickets.
- `jira_add_comment(issue_key, comment)`: Posts automated progress updates, run logs, and PR links.
- `jira_transition_issue(issue_key, transition_name)`: Moves ticket across workflow states (`In Progress`, `Under Review`, `Closed`).

### 3.2 MCP GitHub Server Capabilities
The GitHub MCP server provides declarative tools for code lifecycle management:
- `list_repo_files(branch, path_prefix, extension)`: Maps project file tree and structural patterns.
- `read_file(branch, file_path, start_line, end_line)`: Analyzes context, classes, and dependencies.
- `search_repo_code(keyword)`: Locates symbols, methods, annotations, and references.
- `create_branch(base_branch, new_branch_name)`: Provisions clean feature branches adhering to naming conventions.
- `commit_file_change(branch, file_path, updated_full_content, commit_message)`: Writes validated modifications.
- `create_pull_request(base_branch, head_branch, title, body)`: Submits PRs with cross-linked metadata.

---

## 4. Multi-Agent Collaboration Model

The framework employs specialized agent personas coordinated by an Orchestration State Machine:

1. **Context & Requirement Analyst Agent**:
   - Queries Jira via MCP to parse requirements, constraints, and Acceptance Criteria (AC).
   - Queries the codebase to determine affected modules (e.g. PetClinic Spring components or Python packages).

2. **Planner / Orchestrator Agent**:
   - Decomposes the requirement into actionable implementation steps.
   - Determines dependency graphs and execution order.
   - Monitors loop bounds to prevent infinite retry cycles.

3. **Coding & Test Generation Agent**:
   - Implements minimal, idiomatic changes required to satisfy ACs.
   - Generates accompanying regression and unit tests.
   - Refactors code defensively to prevent breaking existing contracts.

4. **Guardrail & Security Validator**:
   - Enforces pre-commit policies:
     - No credentials, tokens, or private keys committed.
     - No modifications outside permitted repository boundaries.
     - Adherence to static analysis checks (Checkstyle, Ruff, Bandit).

5. **Review & CI/CD Liaison Agent**:
   - Summarizes diffs and changes into a standardized PR template.
   - Captures CI results and logs back to Jira.
   - Translates human reviewer feedback into localized remediation tasks.

---

## 5. Enterprise Guardrails and Safety Policies

To prevent "excessive agency" and protect enterprise systems, the MCP SDLC workflow implements strict policy layers:

| Layer | Policy | Enforcement Mechanism |
|---|---|---|
| **Repository Access** | Branch Protection | Direct push to `main` is disallowed; all changes must route through a feature branch and PR. |
| **Path Restrictions** | Protected Paths | Critical files (`.github/workflows/`, production deployment manifests, database credentials) require elevated permissions. |
| **Secret Scanning** | Zero Hardcoded Secrets | Pre-commit regex and entropy checks block commits containing API tokens, private keys, or passwords. |
| **Loop Bounding** | Max Feedback Iterations | Automated retry loops for test/code fixes are strictly capped (default: 3 iterations) before escalating to human engineers. |
| **Auditability** | Full Event Logging | Every agent decision, MCP tool invocation, and validation result is logged to immutable JSONL audit streams (`events.jsonl`). |

---

## 6. End-to-End Execution Sequence

1. **Trigger**:
   - A Jira issue (e.g., `CAP-101: Add Feature / Fix Bug`) is assigned or triggered via agent webhook.
2. **Context Ingestion**:
   - Orchestrator queries Jira via MCP to fetch issue details.
   - Orchestrator inspects the repository using `list_repo_files` and `read_file`.
3. **Branch Creation**:
   - Branch created: `feat/<issue-id>-<description>` via GitHub MCP `create_branch`.
4. **Development & Verification**:
   - Agent synthesizes code and tests.
   - Local validation checks run (compiler, linter, tests).
5. **Guardrail Validation**:
   - Guardrails inspect proposed diff for secrets and restricted file writes.
6. **Commit & Push**:
   - Code committed via `commit_file_change`.
7. **Pull Request Submission**:
   - Agent invokes `create_pull_request` linking back to the Jira ticket.
8. **Jira Lifecycle Synchronization**:
   - Jira ticket transitioned to `In Review` with a direct link to the newly raised PR.
9. **CI Verification & Human Approval**:
   - GitHub Actions CI workflow triggers automatically.
   - Human code review proceeds; upon merge, Jira ticket is marked `Done`.

---

## 7. Metrics & Productivity Telemetry

The architecture collects operational metrics to evaluate net enterprise productivity:
- **Time to First PR (TTFPR)**: Elapsed time from Jira assignment to opened PR.
- **Rework Rate**: Frequency and count of review iterations required prior to merge.
- **Guardrail Interception Rate**: Count of blocked policy violations (e.g., secret attempts, forbidden paths).
- **Human Review Load Delta**: Quantitative diff size, review time, and reviewer comments compared against human baselines.
