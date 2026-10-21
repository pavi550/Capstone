---
artifact: spec
artifact_schema_version: 1
---

# Agentic SDLC framework — Specification

This file owns the feature's requirements, research, plan, scope, tests, dependencies, and canonical risks. Use team, role, ticket, or system identifiers; do not record personal data.

The sole `closeout complete` checkpoint lives in `index.md`. A real closeout timestamp there seals this file and the whole feature directory; do not duplicate checkpoint rows here.

## Working Description

Implement the flow in `docs/presentation/Agentic_SDLC_Flowdiagram1.html` as the `agentic_sdlc` Python package.
Every node is a stage function; every diamond is a conditional edge; every dashed arrow is a bounded feedback loop.
External systems sit behind interfaces with offline/dry-run defaults and optional live clients.

## Current State Study

- Relevant existing files: `src/__init__.py` (empty), `tests/test_basic.py` (placeholder), `pyproject.toml` (ruff + pytest config), `.github/workflows/ci.yml` (ruff, pytest, bandit on `src`).
- Constraints: Python 3.11 target (CI), 3.13 locally; only PyYAML available locally.

## Implementation Spec

- New files: `src/agentic_sdlc/{__init__,__main__,cli,config,state,events,hitl,llm,jira,knowledge_base,orchestrator,guardrails,repo_adapter,quality,context_engine,ensemble,metrics,vcs,cicd,docs,deploy}.py`, `src/agentic_sdlc/agents/{__init__,coding,testing,review}.py`, `src/agentic_sdlc/prompts/*.md`, `src/agentic_sdlc/sdlc.yaml`, `examples/`, `tests/test_*.py`.
- Updated files: `pyproject.toml`, `requirements-dev.txt`, `.github/workflows/ci.yml`, `README.md`; delete `tests/test_basic.py`; remove `src/__init__.py` (src layout).
- Execution flow (diagram ids): start -> jira/rag/vectordb lane -> gather(1) -> kb(2) -> planner(3) -> repo? (no: concern) -> guard(4) -> per requirement: coder(5a) + tester(5b) -> eval(6) -> passed? (fail: triage -> fixtest/fixcode/fixreq loops) -> all coded? -> per requirement: sec(7a) -> pattern(7b) -> ensemble(7c) -> metrics(8) -> quality? (no: reviewfix loop) -> all reviewed? -> gerrit(9) -> cicd(10) -> regress? (yes: defect loop) -> dod(11) -> deploy(12) -> end.

## Scope

### In Scope

- Every node, decision and loop of the diagram as executable code.
- Optional live clients: Jira REST, ChromaDB vector store, Gerrit push + REST, OpenAI-compatible LLM.
- Example requirements + sample repo; unit tests; CI updated to install the package.

### Out Of Scope

- Quality of LLM-generated code; web dashboard; real Jenkins/Argo CD API clients (CI and deploy run configurable shell commands).

## Success Criteria

- `AC-1` — `py -m agentic_sdlc run --requirements examples/requirements.md --repo examples/sample_repo` reaches `end` and writes `events.jsonl`, `review-<REQ>.json`, `traceability.md`, DoD output.
- `AC-2` — No repo path -> `CONCERN.md` written and run halts at `concern`.
- `AC-3` — Every feedback loop is exercised by a test and bounded by `loops.max_*_iterations`.
- `AC-4` — Guardrails block a hard-coded secret and a write to a restricted path.
- `AC-5` — `py -m agentic_sdlc benchmark` prints accuracy, precision, recall, F1.
- `AC-6` — Live clients (Jira, ChromaDB, Gerrit, LLM) are selected by config and covered by mocked tests.
- `AC-7` — `pytest` passes and `ruff check .` is clean.

## Tests

- Must-have tests: end-to-end happy path; concern path; triage loops (test/code/requirement); review-fix loop; regression-defect loop; loop bounds; guardrails; metrics; repo stack detection; Jira/Gerrit/ChromaDB/LLM clients with mocks.
- Out of scope for testing: real network calls to Jira/Gerrit/LLM (mocked).
- Test framework or runner: pytest.

## Dependencies / Blockers

- Dependencies: PyYAML (required); chromadb, openai optional extras.
- Blockers: none

## Risk Register

| ID | Category | Likelihood | Impact | Mitigation | Residual | Owner |
|---|---|---|---|---|---|---|
| RISK-1 | LLM: excessive agency | med | high | Guardrails gate every file write (restricted paths, secrets); Gerrit push dry-run by default; HITL approval before merge (AC-4) | Live mode relies on correct config | capstone-team |
| RISK-2 | Information disclosure | med | med | Credentials only from env vars, never written to events log | Misconfigured env | capstone-team |

## Activity Log

- `2026-10-20 11:39` Record created; spec confirmed by user with live-integration extension (author: unconfigured; assisted; ref: none; base: none)
- `2026-10-20 12:12` Implementation complete; evidence in build.md (author: unconfigured; assisted; ref: none; base: none)

## Risks And Unknowns

- Live Jira/Gerrit endpoints unavailable for real verification; covered by mocked tests only.
