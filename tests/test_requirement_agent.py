import json

import pytest

from agentic_sdlc.cli import main
from agentic_sdlc.llm import OpenAICompatibleLLM
from agentic_sdlc.specifications import SpecificationStore


@pytest.mark.parametrize("missing, expected", [([], "AWAITING_APPROVAL"), (["Missing limit"], "BLOCKED")])
def test_requirement_analysis_preserves_criteria_and_gates_missing_information(tmp_path, monkeypatch, capsys,
                                                                     missing, expected):
    config = tmp_path / "config.json"
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps([{"key": "CP-1", "fields": {"summary": "Subtract",
        "description": "Acceptance Criteria:\n- Return the difference"}}]), encoding="utf-8")
    config.write_text(json.dumps({"jira": {"issues_file": str(fixture)}, "llm": {
        "provider": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "local-test",
        "api_key_env": "CAPSTONE_TEST_MODEL_KEY",
    }}), encoding="utf-8")
    monkeypatch.setenv("CAPSTONE_TEST_MODEL_KEY", "fixture")

    def complete(self, task, system, user):
        assert task == "requirement"
        assert "untrusted data" in system
        assert json.loads(user)["jira"]["acceptance_criteria"] == [
            {"id": "CP-1-AC-1", "text": "Return the difference"},
        ]
        return json.dumps({"functional": [], "non_functional": [], "security": [],
                           "dependencies": [], "ambiguities": [], "missing_mandatory": missing,
                           "candidate_criteria": []})

    monkeypatch.setattr(OpenAICompatibleLLM, "complete", complete)
    database = tmp_path / "workflow.db"
    assert main(["dev", "spec", "CP-1", "--config", str(config), "--store", str(database),
                 "--allow-provider"]) == 0
    stored = json.loads(capsys.readouterr().out)
    assert SpecificationStore(database).get(stored["run_id"])["status"] == expected
    assert stored["draft"]["analysis"]["missing_mandatory"] == missing
