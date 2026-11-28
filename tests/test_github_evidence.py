import pytest

from agentic_sdlc import github_evidence
from agentic_sdlc.config import Config


@pytest.mark.parametrize("matches", [True, False])
def test_ci_ingestion_requires_matching_attempt_and_sha(monkeypatch, matches):
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    commit = "1" * 40
    calls = []

    def request(method, url, *, headers):
        assert method == "GET" and headers["Authorization"] == "Bearer fixture-token"
        calls.append(url)
        if url.endswith("/attempts/2"):
            return {"id": 7, "run_attempt": 2, "head_sha": commit if matches else "0" * 40,
                    "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "failure",
                    "head_repository": {"full_name": "example/repo"}}
        if "/jobs?" in url:
            return {"total_count": 1, "jobs": [{"id": 9, "run_id": 7, "head_sha": commit,
                                               "name": "tests", "conclusion": "failure"}]}
        return {"total_count": 1, "artifacts": [{"id": 11, "digest": "sha256:" + "2" * 64,
                         "workflow_run": {"id": 7, "head_sha": commit}}]}

    monkeypatch.setattr(github_evidence, "request_json", request)
    args = (Config.load(), "example/repo", 7, 2, commit, "ci.yml")
    if matches:
        result = github_evidence.collect(*args)
        assert result["conclusion"] == "failure"
        assert result["jobs"][0]["conclusion"] == "failure"
        assert result["run_artifact_metadata"][0]["digest"] == "sha256:" + "2" * 64
    else:
        with pytest.raises(ValueError, match="does not match"):
            github_evidence.collect(*args)
        assert len(calls) == 1
