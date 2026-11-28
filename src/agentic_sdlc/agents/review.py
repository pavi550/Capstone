"""7. Review agent: 7a security review, 7b pattern review and an LLM reviewer for the ensemble."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .. import shell
from ..config import Config
from ..context_engine import pattern_review
from ..guardrails import Guardrails
from ..llm import LLM, load_prompt, parse_json
from ..state import Finding, Work
from . import read_files

SAST_RULES = [
    ("eval-exec", "high", r"\b(eval|exec)\s*\(", "eval/exec of dynamic input"),
    ("shell-true", "high", r"shell\s*=\s*True", "subprocess with shell=True (command injection)"),
    ("os-system", "high", r"\bos\.(system|popen)\s*\(", "os.system/popen (command injection)"),
    ("pickle-load", "high", r"\bpickle\.loads?\s*\(", "unsafe deserialisation"),
    ("yaml-load", "medium", r"\byaml\.load\s*\((?![^)]*SafeLoader)", "yaml.load without SafeLoader"),
    ("weak-hash", "medium", r"\bhashlib\.(md5|sha1)\s*\(", "weak hash algorithm"),
    ("tls-verify-off", "high", r"verify\s*=\s*False", "TLS certificate verification disabled"),
    ("sql-format", "high", r"execute\s*\(\s*(f[\"']|[\"'][^\"']*[\"']\s*(%|\.format|\+))",
     "SQL built with string formatting (SQL injection)"),
    ("debug-on", "low", r"\bDEBUG\s*=\s*True", "debug mode enabled"),
]
_COMPILED = [(rid, sev, re.compile(p), msg) for rid, sev, p, msg in SAST_RULES]
_BANDIT_SEVERITY = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}


def _tools(cfg: Config) -> list[str]:
    setting = cfg.get("review.external_tools", "auto")
    if setting == "none":
        return []
    if setting == "auto":
        return [t for t in ("bandit", "pip-audit") if shutil.which(t)]
    return list(setting)


def _bandit(repo: Path, files: list[str]) -> list[Finding]:
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return []
    res = shell.run(["bandit", "-f", "json", "-q", *py], cwd=repo)
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return [
        Finding("security", "sast", _BANDIT_SEVERITY.get(r["issue_severity"], "low"),
                r["issue_text"], Path(r["filename"]).as_posix(), r["line_number"], r["test_id"])
        for r in data.get("results", [])
    ]


def _semgrep(repo: Path, files: list[str]) -> list[Finding]:
    res = shell.run(["semgrep", "--config", "auto", "--json", "-q", *files], cwd=repo)
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return []
    sev = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    return [
        Finding("security", "sast", sev.get(r["extra"].get("severity"), "low"),
                r["extra"].get("message", ""), r["path"], r["start"]["line"], r["check_id"])
        for r in data.get("results", [])
    ]


def _pip_audit(repo: Path) -> list[Finding]:
    reqs = repo / "requirements.txt"
    if not reqs.exists():
        return []
    res = shell.run(["pip-audit", "-r", str(reqs), "-f", "json"], cwd=repo)
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return [
        Finding("security", "dependency", "high",
                f"{d['name']} {d['version']} has {v['id']} (fix: {', '.join(v.get('fix_versions', []))})",
                "requirements.txt", 0, v["id"])
        for d in data.get("dependencies", []) for v in d.get("vulns", [])
    ]


def security_review(repo: Path, work: Work, guardrails: Guardrails, cfg: Config) -> list[Finding]:
    findings: list[Finding] = []
    for rel, text in read_files(repo, work.files + work.test_files).items():
        for lineno, pattern in guardrails.scan_secrets(text):
            findings.append(Finding("security", "secret", "critical",
                                    f"hard-coded secret ({pattern})", rel, lineno, "secret"))
        if rel in work.test_files:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rid, sev, pat, msg in _COMPILED:
                if pat.search(line):
                    findings.append(Finding("security", "sast", sev, msg, rel, lineno, rid))
    tools = _tools(cfg)
    if "bandit" in tools:
        findings += _bandit(repo, work.files)
    if "semgrep" in tools:
        findings += _semgrep(repo, work.files)
    if "pip-audit" in tools:
        findings += _pip_audit(repo)
    return findings


def llm_review(llm: LLM, repo: Path, work: Work) -> list[Finding]:
    req = work.requirement
    prompt = "\n\n".join(load_prompt(p) for p in
                         ("ReviewAgent.md", "SecurityReview.md", "PatternReview.md"))
    user = json.dumps({"requirement": req.text(),
                       "files": read_files(repo, work.files + work.test_files)})
    reply = parse_json(llm.complete("review", prompt, user, {"requirement": req.to_dict()}))
    return [
        Finding("llm", f.get("category", "quality"), f.get("severity", "low"),
                f.get("message", ""), f.get("file", ""), int(f.get("line", 0) or 0),
                f.get("rule", ""))
        for f in reply.get("findings", [])
    ]


class ReviewAgent:
    def __init__(self, llm: LLM, guardrails: Guardrails, cfg: Config):
        self.llm = llm
        self.guardrails = guardrails
        self.cfg = cfg

    def security(self, repo: Path, work: Work) -> list[Finding]:
        return security_review(repo, work, self.guardrails, self.cfg)

    def pattern(self, repo: Path, work: Work) -> tuple[list[Finding], dict]:
        return pattern_review(work.requirement, repo, work.files, work.test_files)

    def llm_vote(self, repo: Path, work: Work) -> list[Finding]:
        return llm_review(self.llm, repo, work)
