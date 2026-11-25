"""Isolated offline development using the existing guarded generation agents."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor
from difflib import unified_diff
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

from .agents import SKIP_DIRS
from .agents.coding import CodingAgent
from .agents.testing import TestAgent
from .guardrails import Guardrails, Violation
from .llm import OpenAICompatibleLLM, StubLLM
from .state import Requirement, Work


class DevelopmentGuardrails(Guardrails):
    def check_write(self, repo, rel, content, tests_only=False):
        denied = super().check_write(repo, rel, content, tests_only=tests_only)
        path = rel.replace("\\", "/").casefold()
        name = path.rsplit("/", 1)[-1]
        if (name.startswith(".env") or path.startswith((".git/", ".github/", "trusted_tests/", "evaluation/"))
                or path in {".git", ".github", "trusted_tests", "evaluation"}):
            denied.append(Violation("trusted_path", rel, "protected enterprise development path"))
        if not tests_only and self.is_test_path(path):
            denied.append(Violation("code_only", rel, "coding agent cannot write test files"))
        return denied


def _entry_is_plain(path: Path, *, directory: bool) -> bool:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return expected(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _snapshot(source: Path, target: Path) -> None:
    if not _entry_is_plain(source, directory=True):
        raise ValueError("Source must be a real directory")
    target.mkdir()
    size = count = 0
    for root, dirs, files in os.walk(source, followlinks=False):
        root = Path(root)
        relative = root.relative_to(source)
        folder = target / relative
        folder.mkdir(exist_ok=True)
        dirs[:] = sorted(name for name in dirs if name.casefold() not in SKIP_DIRS
                         and not name.casefold().startswith(".env"))
        for name in dirs:
            if not _entry_is_plain(root / name, directory=True):
                raise ValueError("Source contains a linked directory")
        for name in sorted(files):
            if name.casefold().startswith(".env") or name.casefold() in {".git", "id_rsa"}:
                continue
            file = root / name
            if not _entry_is_plain(file, directory=False):
                raise ValueError("Source contains a nonregular file")
            count += 1
            size += file.stat().st_size
            if count > 10000 or size > 100_000_000:
                raise ValueError("Offline source snapshot exceeds its file or byte limit")
            shutil.copyfile(file, folder / name)


def _digest(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _manifest(root: Path) -> dict:
    if not _entry_is_plain(root, directory=True):
        raise ValueError("Evidence workspace must be a regular directory")
    results = {}
    for file in sorted(root.rglob("*")):
        directory = file.is_dir()
        if not _entry_is_plain(file, directory=directory):
            raise ValueError("Evidence workspace contains a link or nonregular entry")
        if not directory:
            results[file.relative_to(root).as_posix()] = _digest(file)
    return results


def verify_artifact(evidence: dict) -> None:
    root = Path(evidence["artifact_dir"])
    if not _entry_is_plain(root, directory=True):
        raise ValueError("Development artifact directory is not regular")
    manifest = root / "manifest.json"
    if not _entry_is_plain(manifest, directory=False):
        raise ValueError("Development manifest is not a regular file")
    payload = manifest.read_bytes()
    if hashlib.sha256(payload).hexdigest() != evidence["manifest_sha256"]:
        raise ValueError("Development manifest digest mismatch")
    if "diff_sha256" in evidence:
        patch = root / "generated.diff"
        if not _entry_is_plain(patch, directory=False) or _digest(patch) != evidence["diff_sha256"]:
            raise ValueError("Development diff digest mismatch")
    if _manifest(root / "integrated") != json.loads(payload)["integrated"]:
        raise ValueError("Integrated development artifacts changed since recording")


def _generate(agent, method, work, workspace):
    start = perf_counter()
    files, violations = getattr(agent, method)(work, workspace)
    return files, violations, round((perf_counter() - start) * 1000, 3)


def validate_provider(config, allow_provider: bool) -> None:
    mode = config.get("llm.provider")
    if config.get("knowledge_base.store") != "json":
        raise ValueError("Isolated generation requires the JSON knowledge store")
    if mode == "stub":
        return
    if mode != "openai" or not allow_provider:
        raise ValueError("Configured model generation requires --allow-provider and the OpenAI-compatible adapter")
    endpoint = urlsplit(config.get("llm.base_url") or "")
    if (endpoint.scheme not in {"http", "https"} or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
            or endpoint.username is not None or endpoint.query or endpoint.fragment):
        raise ValueError("Approval-gated generation currently accepts only explicitly configured loopback models")
    model = config.get("llm.model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Configure llm.model for the loopback provider")


def _authorized_context(config, baseline, snapshot, guardrails):
    files = config.get("enterprise.context_files", [])
    if not isinstance(files, list) or len(files) > 5 or not all(isinstance(file, str) for file in files):
        raise ValueError("Authorize up to five exact repository-relative context files")
    for file in files:
        if file not in baseline or file.casefold().startswith((".github/", "trusted_tests/", "evaluation/")):
            raise ValueError("A configured context file is missing or protected")
        if (snapshot / file).stat().st_size > 20000:
            raise ValueError("An authorized context file exceeds 20 KB")
        content = (snapshot / file).read_text(encoding="utf-8")
        if guardrails.scan_secrets(content):
            raise ValueError("An authorized context file matched secret protection rules")
    return tuple(dict.fromkeys(files))


def stage(orchestrator, draft, source: Path, output: Path, run_id: str, attempt: int, *, allow_provider=False) -> dict:
    """Integrate two isolated drafts; leave their code unexecuted for independent verification."""
    from .knowledge_base import KnowledgeBase, make_store

    validate_provider(orchestrator.cfg, allow_provider)
    llm = orchestrator.llm
    real_model = orchestrator.cfg.get("llm.provider") == "openai"
    if type(llm) is not (OpenAICompatibleLLM if real_model else StubLLM):
        raise ValueError("Unexpected provider for this development configuration")
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Use a separate output directory outside the source repository")
    criteria = [criterion["text"] for criterion in draft["acceptance_criteria"]]
    if not criteria or not all(isinstance(value, str) and value.strip() for value in criteria):
        raise ValueError("The approved specification must contain acceptance criteria")
    output.mkdir(parents=True, exist_ok=True)
    root = output / f"{run_id}-{attempt}-{uuid4().hex}"
    root.mkdir(mode=0o700)
    integrated = root / "integrated"
    _snapshot(source, integrated)
    baseline = _manifest(integrated)
    code, tests = root / "code", root / "testcase"
    shutil.copytree(integrated, code)
    shutil.copytree(integrated, tests)
    cfg = orchestrator.cfg
    kb = KnowledgeBase(make_store(cfg), root / "knowledge.json")
    guards = DevelopmentGuardrails(cfg)
    context = _authorized_context(cfg, baseline, integrated, guards)
    coder = CodingAgent(
        orchestrator.llm, guards, kb, context_files=context,
        write_guidance="Return only production source files. The separate test agent owns all test files. "
        "Do not include tests in your JSON even when the requirement specifies tests.",
    )
    tester = TestAgent(orchestrator.llm, guards, kb, context_files=context)

    def work():
        return Work(Requirement(draft["issue"], draft["title"], draft["description"], criteria.copy()))

    start = perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        code_future = pool.submit(_generate, coder, "implement", work(), code)
        test_future = pool.submit(_generate, tester, "write_tests", work(), tests)
        coding = code_future.result()
        testing = test_future.result()
    parallel = round((perf_counter() - start) * 1000, 3)
    evidence = {
        "run_id": run_id, "attempt": attempt, "spec_sha256": draft["spec_sha256"],
        "artifact_dir": str(root), "execution_mode": "CONFIGURED_LOCAL_LLM" if real_model else "OFFLINE_STUB",
        "provider_usage": llm.usage() if real_model else [],
        "authorized_context": {file: baseline[file] for file in context},
        "timings_ms": {"code": coding[2], "testcase": testing[2], "parallel": parallel},
        "guardrail_rules": [violation.rule for violation in coding[1] + testing[1]],
    }
    if evidence["guardrail_rules"]:
        return {**evidence, "status": "BLOCKED"}

    start = perf_counter()
    planned = {}
    for workspace, (reported, _, _) in ((code, coding), (tests, testing)):
        actual = _manifest(workspace)
        # Record only advertised regular files. Fail closed on unexpected mutations.
        changed = {name for name in actual if actual[name] != baseline.get(name)}
        if (not reported or baseline.keys() - actual.keys() or changed - set(reported)
                or set(reported) - actual.keys()):
            return {**evidence, "status": "BLOCKED", "reason": "unexpected or empty agent output"}
        for name in reported:
            if name.casefold() in planned:
                return {**evidence, "status": "BLOCKED", "reason": "conflicting agent outputs"}
            planned[name.casefold()] = (name, workspace)
    differences = []
    for name, workspace in planned.values():
        path = integrated / name
        replacement = workspace / name
        before = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if path.exists() else []
        after = replacement.read_text(encoding="utf-8").splitlines(keepends=True)
        differences.extend(unified_diff(before, after, fromfile=f"a/{name}", tofile=f"b/{name}"))
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(replacement, path)
    patch = root / "generated.diff"
    patch.write_text("".join(differences), encoding="utf-8")
    evidence["diff_sha256"] = _digest(patch)
    evidence["code_files"] = sorted(coding[0])
    evidence["test_files"] = sorted(testing[0])
    evidence["timings_ms"]["integration"] = round((perf_counter() - start) * 1000, 3)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({"baseline": baseline, "integrated": _manifest(integrated)}, sort_keys=True),
        encoding="utf-8",
    )
    return {**evidence, "status": "READY_FOR_VERIFICATION", "manifest_sha256": _digest(manifest)}
