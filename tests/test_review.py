from conftest import EXAMPLES, make_cfg

from agentic_sdlc.agents.review import security_review
from agentic_sdlc.cli import main
from agentic_sdlc.context_engine import pattern_review
from agentic_sdlc.ensemble import Ensemble
from agentic_sdlc.guardrails import Guardrails
from agentic_sdlc.metrics import benchmark, classification, quality_score
from agentic_sdlc.state import Finding, Requirement, Work


def make_work(tmp_path, code: str, test: str | None = "def test_x():\n    pass\n"):
    (tmp_path / "app.py").write_text(code, encoding="utf-8")
    work = Work(Requirement("REQ-1", "Login", acceptance_criteria=["login with password"]))
    work.files = ["app.py"]
    if test is not None:
        (tmp_path / "test_app.py").write_text(test, encoding="utf-8")
        work.test_files = ["test_app.py"]
    return work


def test_security_review_flags_sast_and_secrets(tmp_path):
    cfg = make_cfg()
    code = ("import subprocess\nsubprocess.run(cmd, shell=True)\nx = eval(data)\n"
            'cur.execute(f"SELECT * FROM t WHERE id={uid}")\napi_key = "abcdefgh12345678"\n')
    found = {(f.rule, f.severity) for f in security_review(
        tmp_path, make_work(tmp_path, code), Guardrails(cfg), cfg)}
    assert {("shell-true", "high"), ("eval-exec", "high"), ("sql-format", "high"),
            ("secret", "critical")} <= found


def test_security_review_clean_code(tmp_path):
    cfg = make_cfg()
    work = make_work(tmp_path, "def add(a, b):\n    return a + b\n")
    assert security_review(tmp_path, work, Guardrails(cfg), cfg) == []


def test_pattern_review_traceability(tmp_path):
    traced = make_work(tmp_path, '"""REQ-1: login with password"""\n')
    findings, trace = pattern_review(traced.requirement, tmp_path, traced.files, traced.test_files)
    assert trace["covered"] == ["login with password"] and findings == []

    untraced = make_work(tmp_path, "x = 1\n", test=None)
    findings, trace = pattern_review(untraced.requirement, tmp_path, untraced.files, [])
    assert {f.rule for f in findings} == {"ac-not-traced", "no-tests", "id-missing"}


def test_ensemble_consensus_removes_noise():
    ens = Ensemble(make_cfg())
    rules_low = Finding("security", "sast", "low", "m", "a.py", 10, "r1")
    llm_same = Finding("llm", "sast", "medium", "m", "a.py", 12, "x")
    llm_noise = Finding("llm", "quality", "low", "nit", "a.py", 50, "style")
    llm_high = Finding("llm", "security", "high", "authz", "b.py", 3, "authz")
    groups = ens.vote({"rules": [rules_low], "llm": [llm_same, llm_noise, llm_high]})
    kept = {g.representative.rule for g in groups if g.kept}
    assert len(groups) == 3
    assert kept == {"x", "authz"}  # agreed finding + always-keep high; llm-only nit dropped


def test_quality_score_and_classification():
    assert quality_score([]) == 1.0
    assert quality_score([Finding("r", "c", "high", "m"), Finding("r", "c", "medium", "m")]) == 0.3
    m = classification(tp=2, fp=1, fn=1, tn=2)
    assert m["precision"] == m["recall"] == m["f1"] == 0.6667 and m["accuracy"] == 0.6667


def test_benchmark_from_dataset():
    result = benchmark(dataset=EXAMPLES / "benchmark.json")
    assert result["items"] == 6
    assert result["reviewers"]["rules"]["f1"] == 0.6667


def test_benchmark_cli(capsys):
    assert main(["benchmark", "--dataset", str(EXAMPLES / "benchmark.json")]) == 0
    out = capsys.readouterr().out
    for metric in ("accuracy=", "precision=", "recall=", "f1="):
        assert metric in out
