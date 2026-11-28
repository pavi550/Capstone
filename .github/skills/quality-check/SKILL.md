---
name: quality-check
description: Present the completed Jira-to-code change and test evidence for human review.
disable-model-invocation: true
---

# Final human review

Use this skill only when a person explicitly invokes `/quality-check`, or as
the final review stage of `/requirement`. This is the human checkpoint after
the Jira issue has been read, acceptance criteria created, code and tests
updated, and relevant tests run.

Review the current change read-only. Compare the diff with the Jira-derived
acceptance criteria and test results. Present:

- the Jira issue key and concise requirement summary;
- each acceptance criterion and its implementation/test mapping;
- changed application and test files;
- exact tests run and their pass/fail/blocked status;
- security, regression, scope, or environment risks still open.

Ask the human to approve, reject, or request changes. Do not infer approval
from passing tests. Do not commit, push, merge, change Jira, or deploy. A
conversational decision is not a digest-bound enterprise approval receipt.

If the user explicitly started the separate enterprise CLI lifecycle, follow
that lifecycle's recorded approval gates instead; do not mix the two workflows.
