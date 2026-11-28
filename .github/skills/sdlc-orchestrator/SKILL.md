---
name: sdlc-orchestrator
description: Coordinate the Capstone SDLC stages with explicit human decisions.
disable-model-invocation: true
---

# Human-controlled SDLC coordinator

Use this skill only when a person explicitly invokes `/sdlc-orchestrator`.
Follow [the framework guide](../../../docs/AI-SDLC-Framework-Guide.md) and
[enterprise implementation status](../../../docs/enterprise-implementation.md).

This is a coordinator, not an autonomous approval agent:

- Treat the command-line engine as an internal implementation detail. Execute
  its operations yourself with the available terminal tool; never ask the user
  to run raw `agentic_sdlc` commands or present them as another workflow.
  Do not show command syntax or flags in chat. Communicate the next action
  using the relevant slash command.
- Ask which Jira issue, source repository, trusted policy file, state database,
  and output directory to use. Do not guess paths, issue keys, identities, or
  acceptance criteria.
- Run the existing `python -m agentic_sdlc dev` commands internally. Prefer the
  repository virtual environment (`.venv/bin/python`, or
  `.venv/Scripts/python.exe` on Windows). If setup is missing, direct the user
  to the setup instructions in the framework guide; do not install
  dependencies or configure credentials without permission.
- Keep the SQLite approval store and generated output outside the source
  repository. Never request or print API tokens or signing keys.
- Run one stage at a time. Show the result, files, digests, findings,
  and unknowns. Then stop and ask the user to approve, reject, or request
  changes before moving to the next stage.
- Never infer approval from silence, a passing test, an agent's confidence, or
  a successful engine exit code. Record digest-bound approval or rejection
  only after the user explicitly decides.
- Do not lower quality thresholds, suppress tests, hide findings, or mark
  unavailable evidence as passing.

Suggested sequence:

1. `/requirement` to draft the Jira specification and obtain requirement
   approval.
2. `/dev-code` to start the integrated workflow from the approved Jira
   specification, generate isolated code and test drafts, inspect their
   combined manifest, and explicitly approve or reject it.
3. `/dev-testing` to run independent checks after artifact approval, then
   `/quality-check` to inspect results and explicitly approve or reject the
   exact quality digest.
4. `/dev-review` to run security, pattern, and configured model review, inspect
   findings, and explicitly approve or reject the review digest.
5. Resume the workflow only after final review approval. If failures require
   correction, ask the human to explicitly authorize one bounded correction
   cycle, then record that authorization internally. Before any merge, CI
   write, or deployment, explain the target and impact and obtain separate
   explicit human authorization.

When continuing an existing approved specification, use the same workflow
run; otherwise start with the Jira issue key and expect a requirement
approval pause. Do not substitute standalone draft generation for the
integrated approval-bound artifact. Keep the same workflow run, trusted
policy, output paths, provider authorization, and verifier identity when
resuming. Do not ask the user to run a backend command to continue.
