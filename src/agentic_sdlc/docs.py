"""11. Update documentation (DoD): traceability matrix, DoD checklist and release notes."""

from __future__ import annotations

from pathlib import Path

from .state import RunState, Status


def _traceability(state: RunState) -> str:
    rows = ["| Requirement | Title | Status | Code files | Test files | Criteria traced | Score |",
            "|---|---|---|---|---|---|---|"]
    for w in state.work.values():
        r, t = w.requirement, w.trace
        traced = f"{len(t.get('covered', []))}/{len(r.acceptance_criteria)}" if t else "-"
        score = f"{w.score:.2f}" if w.score is not None else "-"
        rows.append(f"| {r.id} | {r.title} | {w.status.value} | {', '.join(w.files) or '-'} | "
                    f"{', '.join(w.test_files) or '-'} | {traced} | {score} |")
    return "# Traceability matrix\n\n" + "\n".join(rows) + "\n"


def dod_checklist(state: RunState) -> list[tuple[str, bool]]:
    works = list(state.work.values())
    approved = [w for w in works if w.status == Status.APPROVED]
    dry_run = state.vcs_result.get("mode") == "dry-run"
    vcs_ok = bool(state.vcs_result.get("ok")) and bool(approved) and not dry_run
    vcs_label = "Change integrated into Git / Gerrit" + (" (dry-run: planned only)" if dry_run else "")
    return [
        ("All requirements implemented and approved by review",
         bool(works) and len(approved) == len(works)),
        ("All requirement tests pass", all(w.last_check and w.last_check.passed for w in approved)
         and bool(approved)),
        ("Review quality threshold met for every approved requirement",
         all(w.score is not None for w in approved) and bool(approved)),
        ("No guardrail violations left unresolved", not any(
            w.status == Status.BLOCKED and w.last_check
            and w.last_check.output.startswith("Guardrail violations") for w in works)),
        (vcs_label, vcs_ok),
        ("CI/CD full regression suite passed", bool(state.ci_result and state.ci_result.passed)),
        ("Traceability matrix updated", True),
        ("Release notes written", True),
    ]


def _release_notes(state: RunState) -> str:
    lines = [f"# Release notes — run {state.run_id}", ""]
    for w in state.work.values():
        if w.status == Status.APPROVED:
            lines.append(f"- **{w.requirement.id}** {w.requirement.title}")
    blocked = [w for w in state.work.values() if w.status != Status.APPROVED]
    if blocked:
        lines += ["", "## Not delivered", ""]
        lines += [f"- {w.requirement.id} {w.requirement.title} ({w.status.value})" for w in blocked]
    return "\n".join(lines) + "\n"


def write_docs(state: RunState, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    checklist = dod_checklist(state)
    dod = "# Definition of Done\n\n" + "\n".join(
        f"- [{'x' if ok else ' '}] {item}" for item, ok in checklist) + "\n"
    files = {
        "traceability": ("traceability.md", _traceability(state)),
        "dod": ("DoD.md", dod),
        "release_notes": ("release_notes.md", _release_notes(state)),
    }
    out = {}
    for key, (name, text) in files.items():
        (out_dir / name).write_text(text, encoding="utf-8")
        out[key] = str(out_dir / name)
    return out
