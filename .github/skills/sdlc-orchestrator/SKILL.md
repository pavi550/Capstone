---
name: sdlc-orchestrator
description: Coordinate the Capstone SDLC stages with explicit human decisions and existing agentic_sdlc CLI evidence.
disable-model-invocation: true
---

# Human-controlled SDLC coordinator

Use this skill only when a person explicitly invokes `/sdlc-orchestrator`.
Follow [the framework guide](../../../docs/AI-SDLC-Framework-Guide.md) and
[enterprise implementation status](../../../docs/enterprise-implementation.md).

This is a coordinator, not an autonomous approval agent:

- Ask which Jira issue, source repository, trusted policy file, state database,
  and output directory to use. Do not guess paths, issue keys, identities, or
  acceptance criteria.
- Use the existing `python -m agentic_sdlc dev` commands. Prefer the
  repository virtual environment (`.venv/bin/python`, or
  `.venv/Scripts/python.exe` on Windows). If setup is missing, tell the user
  to run `bash setup_env.sh`; do not install dependencies or configure
  credentials without permission.
- Keep the SQLite approval store and generated output outside the source
  repository. Never request or print API tokens or signing keys.
- Run one stage at a time. Show the command result, files, digests, findings,
  and unknowns. Then stop and ask the user to approve, reject, or request
  changes before moving to the next stage.
- Never infer approval from silence, a passing test, an agent's confidence, or
  a successful CLI exit code. Use digest-bound `dev approve` / `dev reject`
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
5. Resume the workflow only after final review approval. Failures pause for
   an explicit human correction decision; `--authorize-correction` allows one
   bounded cycle. Before any merge, CI write, or deployment, explain the
   target and impact and obtain separate explicit human authorization.

Use `dev workflow-start --run-id RUN_ID` to reuse an existing stored
specification; otherwise start with the Jira issue key and expect the
workflow to pause for requirement approval. Do not substitute standalone
`dev code` or `dev testcase` drafts for the integrated approval-bound
artifact. Keep the same workflow run, trusted policy, output paths, provider
authorization, and verifier identity when resuming.
