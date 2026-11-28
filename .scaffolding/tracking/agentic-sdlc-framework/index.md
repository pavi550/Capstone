---
schema_version: 3
artifact: index
artifact_schema_version: 1
title: Agentic SDLC framework
slug: agentic-sdlc-framework
status: implemented
source: direct-user-request
owner: capstone-team
issue: pending
pull_request: pending
started: 2026-10-20 11:39
last_updated: 2026-10-20 12:12
blocked_by: none
current_checkpoint: implementation complete
goal_lock:
  status: active
  stamped: 2026-10-20 11:39
  scope:
    - src/agentic_sdlc/ package implementing every node, decision and loop of Agentic_SDLC_Flowdiagram1.html
    - optional live integrations (Jira REST, ChromaDB, Gerrit push / REST) behind config + env vars
    - offline LLM stub + optional OpenAI-compatible client
    - examples/, tests/, pyproject.toml, requirements-dev.txt, ci.yml, README usage section
  success_criteria: [AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7]
---

# Agentic SDLC framework

This index is the identity and lifecycle rollup for one schema-v3 feature directory. Use team, role, ticket, or system identifiers; do not record personal data.

`closeout complete` is stamped only in this file. Once it has a real timestamp, the entire feature directory is sealed and none of its files may be edited.

For new checkpoint details and Decisions, append repository-safe attribution when the format permits: Git author name; `assisted` or `human`; branch or detached reference; **pre-write HEAD/base commit**; and any existing PR/MR URL. If Git author name is absent, warn visibly once, use `author: unconfigured`, and continue without blocking. Never record email or absolute worktree paths.

Before a checkpoint or lifecycle write, inspect the feature for unresolved paths and conflict markers. If any conflict remains unresolved, leave checkpoint and lifecycle state unchanged and do not stamp. Source development may continue while tracking resolution is pending.

## Feature Definition

- One-line goal: `Build a runnable Python framework that implements the Agentic SDLC flow (requirements -> orchestrator -> guardrails -> coding/test agents -> review -> Gerrit -> CI/CD -> DoD -> deploy) with every decision and feedback loop.`
- Problem: `The repository only contains docs and diagrams; there is no executable framework behind the documented Agentic SDLC flow.`
- Requested by: `capstone-team (direct request)`
- Related links:
  - `docs/presentation/Agentic_SDLC_Flowdiagram1.html`

## Navigation

- [Specification](./spec.md) — created with the index at `start`.
- [Build](./build.md) — created at `implementation started`.
- `closeout.md` — created when reflection or closeout begins.

## Checkpoints

Each checkpoint is stamped via `/tracking`. Always obtain the current timestamp; never infer one. Use `back-filled` only when work predates the record and the exact time cannot be recovered.

- [x] start | completed: `2026-10-20 11:39` (author: unconfigured; assisted; ref: none — not a git repo; base: none)
- [x] spec / plan created | completed: `2026-10-20 11:39` (author: unconfigured; assisted; ref: none; base: none)
- [ ] threat-model stamped | completed: `pending`
- [x] implementation started | completed: `2026-10-20 11:40` (author: unconfigured; assisted; ref: none; base: none)
- [x] implementation complete | completed: `2026-10-20 12:12` (author: unconfigured; assisted; ref: none; base: none)
- [ ] critic pass | completed: `pending`
- [ ] risk-register reviewed | completed: `pending`
- [ ] feature validated | completed: `pending`
- [ ] PR created | completed: `pending`
- [ ] closeout complete | completed: `pending`

## Notes

- Workspace is not a git repository, so the usual `tracking:` bookkeeping commits could not be made.

## Decisions

- Orchestrator is a pure-Python state machine shaped like a LangGraph graph (nodes + conditional edges), LangGraph optional — avoids a heavy required dependency and keeps CI simple. (author: unconfigured; assisted)
- LLM access: offline deterministic stub by default, optional OpenAI-compatible client (OpenAI/Azure/Ollama/vLLM via base_url) when an API key is configured — framework runs and is testable without credentials. (author: unconfigured; assisted)
- Live integrations (Jira REST, ChromaDB, Gerrit push/REST) are in scope but opt-in via `sdlc.yaml` + env vars; offline/dry-run remains default — user requested live integrations; defaults keep tests hermetic. (author: unconfigured; assisted)
