---
name: dev-testing
description: Run independent verification for a human-approved integrated artifact.
disable-model-invocation: true
---

# Independent testing and quality gate

Use this skill only when a person explicitly invokes `/dev-testing`. Continue
the integrated enterprise workflow only after the human has approved its
combined code/test manifest. Treat approved acceptance criteria as the test
oracle. Do not rely on the coding agent's explanation, make tests artificially
pass, weaken assertions, or suppress/skip failing tests.

Run the backend operations yourself. Do not ask the user to run or copy
`agentic_sdlc` commands, and do not show command syntax or flags in chat.
Communicate only the next slash-command action.

Ask for the workflow `run_id`, approval store, trusted policy YAML, verifier
identity, independent acceptance-test directory, and whether configured model
use was authorized for the workflow. Confirm the integrated artifact and its
development approval are present before running checks.

Resume the enterprise workflow to run independent checks:

```text
python -m agentic_sdlc dev workflow-resume --store STORE --run-id RUN_ID
```

If the run used a configured provider, use it only when it was explicitly
authorized and used when starting the workflow. The stored
workflow plan supplies its original acceptance directory and verifier
identity; do not substitute a different test target. Report each recorded
check, pass/fail outcome, evidence, and verification digest. Stop at
`quality: WAITING_FOR_APPROVAL`. Do not make a quality decision; direct the
user to `/quality-check` to inspect and decide on the recorded evidence.
If checks fail, stop at the correction pause. Ask the human to explicitly
authorize a correction before allowing the model to modify the artifact.
Record the authorization internally; do not ask the user to run a CLI flag.

Do not modify code or tests, skip failing checks, or claim review/deployment
approval.
