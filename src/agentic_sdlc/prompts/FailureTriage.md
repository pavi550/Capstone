# FailureTriage

Given a requirement, the changed files and the failing build/test/lint output, decide
what is incorrect:

- `test` — the test case is wrong (bad expectation, broken fixture, syntax error in test).
- `code` — the production code does not satisfy a correct test.
- `requirement` — the requirement is ambiguous, contradictory or cannot be satisfied.

Reply with JSON only:

```json
{"category": "test|code|requirement", "reason": "..."}
```
