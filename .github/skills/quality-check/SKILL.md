---
name: quality-check
description: Review verification evidence and record the human quality decision.
disable-model-invocation: true
---

# Quality decision agent

Use this skill only when a person explicitly invokes `/quality-check`. Do not
run or alter checks, edit implementation or tests, skip failures, or lower
thresholds. This skill evaluates already-recorded independent verification
evidence; `/dev-testing` is responsible for executing the approved checks.

Ask for the workflow run ID, trusted approval store, policy configuration,
and decision-maker identity. Read the persisted workflow status and report:

```text
python -m agentic_sdlc dev status --store STORE --run-id RUN_ID
python -m agentic_sdlc dev report --store STORE --run-id RUN_ID
```

Proceed only when the workflow is waiting at the quality approval gate.
Present the exact verification digest, recorded status for every independent
check, first-pass score, failures, coverage gaps, blocked findings, and
unavailable evidence. A failed result is a failure and cannot be approved.
Do not use generated tests as the only independent ground truth.

Ask for the human's explicit decision. Only after approval, record the
quality decision with:

```text
python -m agentic_sdlc dev approve --store STORE --run-id RUN_ID --quality-sha256 DIGEST --stage quality --actor APPROVER --config POLICY
```

Reject with `dev reject` using the same digest and the user's reason. Never
approve a failed result or advance to review on the user's behalf. If the
human rejects passing evidence and requests a correction, resume only with
`--authorize-correction` and the original provider authorization; this
authorizes one bounded correction cycle, and the corrected artifact must pass
through development approval and independent verification again.
