---
name: dev-review
description: Review code, tests, and quality evidence and request human adjudication.
disable-model-invocation: true
---

# Independent review agent

Use this skill only when a person explicitly invokes `/dev-review`. Review the
approved requirement, production diff, tests, and available quality evidence.
Do not edit files, discard test failures, or treat an earlier agent's
conclusion as ground truth. Report security, correctness, maintainability,
test-coverage, and requirement-traceability findings with severity and
evidence.

Run the backend operations yourself. Do not ask the user to run or copy
`agentic_sdlc` commands, and do not show command syntax or flags in chat.
Communicate only the next slash-command action.

For a passed, integrated enterprise run whose independent quality result has
been approved, resume the workflow:

```text
python -m agentic_sdlc dev workflow-resume --store STORE --run-id RUN_ID
```

Use a configured review model only if the workflow was started with that
explicit authorization and the model is needed.
The workflow will run security, pattern, and configured model review and pause
at final review approval. Present the exact `review_sha256`, blocking
findings, score, and missing evidence. Then stop and ask the user to approve
or reject that exact review. Only after explicit approval, record it using:

```text
python -m agentic_sdlc dev approve --store STORE --run-id RUN_ID --review-sha256 REVIEW_SHA256 --stage review --actor APPROVER --config POLICY
```

The approval actor must satisfy the configured separation-of-duties policy.
For rejection, record the user's reason against the same review digest.
After an approved review, resume the workflow only with explicit
authorization for any required CI or remote DEV action. Never approve a
blocked review or silently trigger corrections. For a human-requested
correction, record explicit authorization internally and use the original
provider authorization for one bounded cycle. Never trigger merge, CI writes,
or deployment without separate approval.

For a manual workspace without enterprise receipts, perform read-only review
and label the result as advisory, not as digest-bound final approval.
