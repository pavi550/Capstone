---
name: dev-code
description: Generate and present the integrated code/test artifact for human approval.
disable-model-invocation: true
---

# Integrated development artifact

Use this skill only when a person explicitly invokes `/dev-code`. Do not
broaden the approved requirement. The enterprise workflow creates production
code and independent tests in isolated workspaces; do not use the standalone
`dev code` draft command for the approval-bound workflow.

Run the backend operations yourself. Do not ask the user to run or copy
`agentic_sdlc` commands, and do not show command syntax or flags in chat.
Communicate only the next slash-command action.

Ask for the approved requirement `run_id`, approval store, trusted policy
YAML, source repository path, separate output directory, trusted independent
acceptance-test directory, verifier identity, and whether the user explicitly
authorizes a configured model provider. Do not request API keys in chat.

First confirm that the stored requirement is approved:

```text
python -m agentic_sdlc dev status --store STORE --run-id RUN_ID
```

Start the integrated code-and-test generation using the existing approved
specification:

```text
python -m agentic_sdlc dev workflow-start --run-id RUN_ID --store STORE --config POLICY --repo SOURCE --out OUTPUT --acceptance-dir ACCEPTANCE_DIR --actor VERIFIER
```

Add `--allow-provider` only after the user explicitly authorizes use of the
configured provider. Show the approved requirement, combined manifest digest,
code and test file paths, output workspace, and workflow status. Stop at
`WAITING_FOR_APPROVAL`. Ask the user to inspect the exact integrated artifact.
Only after explicit approval record the decision:

```text
python -m agentic_sdlc dev approve --store STORE --run-id RUN_ID --development-sha256 MANIFEST_SHA256 --stage development --actor APPROVER --config POLICY
```

For rejection, record the user's reason against the same digest. If the user
requests a model-backed correction, obtain explicit confirmation and record
the authorization internally, then use the same provider authorization used
at workflow start. This authorizes one bounded correction cycle, creates a new
manifest, and returns to this approval gate. Do not run testing, review,
merge, or deployment from this skill. Do not claim tests passed or release
gates were satisfied.
