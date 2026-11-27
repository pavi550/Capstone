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

For a passed, integrated enterprise run whose independent quality result has
been approved, resume the workflow:

```text
python -m agentic_sdlc dev workflow-resume --store STORE --run-id RUN_ID
```

Add `--allow-provider` only if the workflow was started with that explicit
authorization and the configured review model is needed.
The workflow will run security, pattern, and configured model review and pause
at final review approval. Present the exact `review_sha256`, blocking
findings, score, and missing evidence. Then stop and ask the user to approve
or reject that exact review. Only after explicit approval, record it using:

```text
python -m agentic_sdlc dev approve --store STORE --run-id RUN_ID --review-sha256 REVIEW_SHA256 --stage review --actor APPROVER --config POLICY
```

The approval actor must satisfy the configured separation-of-duties policy.
For rejection, use `dev reject` with the same review digest and the user's
reason. After an approved review, resume the workflow only with explicit
authorization for any required CI or remote DEV action. Never approve a
blocked review or silently trigger corrections. A human-requested correction
must use `workflow-resume --authorize-correction` and the original provider
authorization; it authorizes one bounded correction cycle. Never trigger
merge, CI writes, or deployment without separate approval.

For a manual workspace without enterprise receipts, perform read-only review
and label the result as advisory, not as digest-bound final approval.
