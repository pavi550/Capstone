---
name: dev-review
description: Review code, tests, and quality evidence and request human adjudication.
disable-model-invocation: true
---

# Pre-review code review

Use this skill only when a person explicitly invokes `/dev-review`, or as the
read-only review step after `/dev-testing` in the Jira-to-application
workflow. Review the Jira-derived acceptance criteria, application diff,
tests, and actual test output. Do not edit files, discard failures, or treat
an earlier agent's conclusion as ground truth. Report security, correctness,
maintainability, test-coverage, and requirement-traceability findings with
severity and concrete evidence.

This review is advisory input to the final human checkpoint; it is not a
digest-bound enterprise approval. Do not commit, push, merge, update Jira, or
deploy.

If the user explicitly started the separate enterprise CLI lifecycle, follow
its stored workflow and approval gates instead of this pre-review path. Do not
mix the two workflows.
