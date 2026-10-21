"""7b. Requirement-pattern review: code <-> requirement traceability."""

from __future__ import annotations

import re
from pathlib import Path

from .agents import read_files
from .state import Finding, Requirement

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "shall", "should", "must", "will", "when",
    "are", "from", "into", "each", "every", "have", "has", "can", "not", "all", "any", "its",
    "user", "users", "system", "given", "then",
}
COVERAGE_RATIO = 0.6


def keywords(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3} - STOPWORDS


def trace(req: Requirement, repo: Path, code_files: list[str], test_files: list[str]) -> dict:
    code = "\n".join(read_files(repo, code_files).values())
    tests = "\n".join(read_files(repo, test_files).values())
    corpus = (code + "\n" + tests).lower()
    corpus_words = keywords(corpus)
    covered, missing = [], []
    for criterion in req.acceptance_criteria:
        kws = keywords(criterion)
        hit = criterion.lower() in corpus or (
            bool(kws) and len(kws & corpus_words) / len(kws) >= COVERAGE_RATIO)
        (covered if hit else missing).append(criterion)
    return {
        "requirement": req.id,
        "id_referenced": req.id.lower() in corpus,
        "has_tests": bool(test_files),
        "covered": covered,
        "missing": missing,
    }


def pattern_review(req: Requirement, repo: Path, code_files: list[str],
                   test_files: list[str]) -> tuple[list[Finding], dict]:
    t = trace(req, repo, code_files, test_files)
    main = code_files[0] if code_files else ""
    findings = [
        Finding("pattern", "traceability", "low", f"acceptance criterion not traceable: {c}",
                main, 0, "ac-not-traced")
        for c in t["missing"]
    ]
    if not t["has_tests"]:
        findings.append(Finding("pattern", "traceability", "medium",
                                "no tests written for this requirement", main, 0, "no-tests"))
    if not t["id_referenced"]:
        findings.append(Finding("pattern", "traceability", "info",
                                f"{req.id} is not referenced in the change", main, 0, "id-missing"))
    return findings, t
