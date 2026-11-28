---
name: dev-testing
description: Run the target application's relevant tests before presenting the change for human review.
disable-model-invocation: true
---

# Application testing

Use this skill only when a person explicitly invokes `/dev-testing`, or as
the testing stage of `/requirement`. Testing is performed after the code and
focused tests are generated, but before the final human review.

Resolve the same target repository used by `/dev-code`. Verify the current
branch and working tree; do not switch branches or overwrite files. Inspect
the project's existing test and build instructions and use its checked-in
wrapper. Do not install dependencies, alter build files, suppress checks, or
edit tests simply to get a passing result.

For Spring PetClinic, Java 17 or newer is required. Run the relevant focused
tests first when practical, then the relevant Maven or Gradle suite. Capture
the exact command, exit status, and meaningful output. If Java, Docker, a
service, or another prerequisite is unavailable, state which checks were
blocked and why; never report blocked or skipped checks as passing.

Report test results against the acceptance criteria. Do not edit application
code in this skill. Return the target branch, changed-file list, commands,
pass/fail/blocked results, and remaining gaps to the calling workflow. The
human review happens after this report.
