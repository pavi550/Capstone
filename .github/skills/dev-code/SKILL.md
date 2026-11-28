---
name: dev-code
description: Implement Jira-backed acceptance criteria in the configured application repository.
disable-model-invocation: true
---

# Application implementation

Use this skill only when a person explicitly invokes `/dev-code`, or as the
implementation stage of `/requirement`. Do not broaden the Jira requirement.

Use the acceptance criteria already established in the current conversation.
If they are missing, ask for the Jira issue key or user-authored criteria;
never invent requirements. Use the target configured by
`CAPSTONE_TARGET_REPO`, or ask for a path if it is unset. Confirm the target
is the root of the independent PetClinic checkout.

Before editing, inspect the target Git root, branch, status, and diffs for
files in scope. Work on the existing branch. Preserve unrelated local changes.
If an in-scope file has conflicting user changes, stop and ask before editing.

Implement the narrowest change that satisfies the acceptance criteria. Add or
update focused tests that cover each criterion and relevant edge cases. Keep
framework files in Capstone and application code/tests in PetClinic. Do not
commit, push, switch branches, edit Jira, modify CI/secrets, or change build
configuration unless the requirement specifically calls for it and the user
authorizes it.

Do not run generated tests in this skill. Hand the changed files and
acceptance-to-test mapping to `/dev-testing`. The end-to-end `/requirement`
workflow runs testing next, then presents the combined result to the human.
