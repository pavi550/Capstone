---
artifact: build
artifact_schema_version: 1
---

# Agentic SDLC framework — Build

## Implementation Notes

- Package `src/agentic_sdlc/` (src layout, `pip install -e .`); removed placeholder `src/__init__.py` and `tests/test_basic.py`.
- Orchestrator graph node names map 1:1 to diagram boxes/diamonds; loops bounded by `loops.max_fix_iterations`, `max_review_iterations`, `max_ci_iterations`, plus `max_steps`.
- Added `repo.<step>_command: none` to disable a detected command (used by tests for speed).
- Behaviour noted: a requirement BLOCKED after max fix iterations leaves its files in the working tree, so CI regression then fails and the run halts with a concern (no rollback implemented).

## Verification Evidence (2026-10-20 12:12)

- `py -m pytest -q` -> `55 passed in 131.03s` (AC-1..AC-6 covered by tests/test_end_to_end.py, test_loops.py, test_guardrails.py, test_review.py, test_integrations.py)
- `py -m ruff check .` -> `All checks passed!` (AC-7)
- `py -m bandit -r src -q` -> exit 0, no findings
- `py -m agentic_sdlc run --requirements examples\requirements.md --repo <copy of sample_repo>` -> `finished at 'end'`, REQ-001/REQ-002 approved, exit 0 (AC-1)
- Same command without `--repo` -> `finished at 'halt'`, `CONCERN.md` written, exit 2 (AC-2)
- `py -m agentic_sdlc benchmark --dataset examples\benchmark.json` -> accuracy/precision/recall/F1 = 0.667 for both reviewers (AC-5)

## PR Updates

- none (workspace is not a git repository)
