"""Read GitHub Actions run evidence for an explicitly selected commit and attempt."""

import hashlib
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .httpclient import HttpError, _NoRedirect, request_json


def collect(config, repository, run_id, attempt, commit, workflow):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise ValueError("Use an owner/repository pair for GitHub Actions evidence")
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Supply the full lowercase Git commit SHA")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.ya?ml", workflow) or run_id < 1 or attempt < 1:
        raise ValueError("Supply the workflow filename and positive run and attempt IDs")
    token = config.secret("github.token_env")
    if not token:
        raise ValueError("Configure GITHUB_TOKEN with Actions read access to the selected repository")
    base = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}"
    headers = {"Authorization": f"Bearer {token}"}

    def get(suffix):
        try:
            result = request_json("GET", f"{base}{suffix}", headers=headers)
        except HttpError as exc:
            raise RuntimeError(f"GitHub Actions evidence read failed (HTTP {exc.status})") from None
        if not isinstance(result, dict):
            raise TypeError("GitHub Actions returned an invalid evidence response")
        return result

    def records(suffix, field):
        response = get(f"{suffix}?per_page=100")
        items = response[field]
        if not isinstance(items, list) or response["total_count"] != len(items) or len(items) > 100:
            raise ValueError(f"GitHub Actions {field} are incomplete or exceed the ingestion limit")
        return items

    path = f".github/workflows/{workflow}"
    run = get(f"/attempts/{attempt}")
    head_repository = (run.get("head_repository") or {}).get("full_name", "")
    if (run.get("id") != run_id or run.get("run_attempt") != attempt or run.get("head_sha") != commit
            or str(run.get("path", "")).partition("@")[0] != path or run.get("status") != "completed"
            or head_repository.casefold() != repository.casefold()):
        raise ValueError("GitHub Actions evidence does not match the selected workflow, repository, commit and attempt")
    jobs = records(f"/attempts/{attempt}/jobs", "jobs")
    if any(job.get("run_id") != run_id or job.get("head_sha") != commit for job in jobs):
        raise ValueError("GitHub Actions job evidence does not match the selected run and commit")
    # The artifact endpoint is run-scoped. Keep metadata distinct from attempt-scoped jobs.
    artifacts = []
    for artifact in records("/artifacts", "artifacts"):
        origin = artifact.get("workflow_run") or {}
        if origin.get("id") != run_id or origin.get("head_sha") != commit:
            raise ValueError("GitHub Actions artifact metadata does not match the selected run and commit")
        artifacts.append({key: artifact.get(key) for key in ("id", "name", "digest", "expired", "size_in_bytes")})
    return {
        "repository": repository, "run_id": run_id, "attempt": attempt, "commit": commit,
        "workflow": path, "event": run.get("event"), "conclusion": run.get("conclusion"),
        "observed_at": datetime.now(UTC).isoformat(),
        "jobs": [{key: job.get(key) for key in ("id", "name", "status", "conclusion", "started_at", "completed_at")}
                 for job in jobs],
        "run_artifact_metadata": artifacts,
    }


def download_artifact(config, evidence, artifact_id, output):
    repository = evidence.get("repository", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise ValueError("Invalid evidence repository")
    artifact = next((row for row in evidence["run_artifact_metadata"] if row["id"] == artifact_id), None)
    if artifact is None or artifact.get("expired") or not re.fullmatch(r"sha256:[a-f0-9]{64}", artifact.get("digest") or ""):
        raise ValueError("Artifact must have an unexpired GitHub SHA-256 digest")
    token = config.secret("github.token_env")
    if not token:
        raise ValueError("Configure GITHUB_TOKEN with Actions read access")
    limit = 50_000_000
    declared = artifact.get("size_in_bytes")
    if type(declared) is not int or not 0 < declared <= limit:
        raise ValueError("Artifact size is missing or exceeds the download limit")
    metadata_url = f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}"
    try:
        remote = request_json("GET", metadata_url, headers={"Authorization": f"Bearer {token}"})
    except HttpError as exc:
        raise RuntimeError(f"GitHub artifact metadata read failed (HTTP {exc.status})") from None
    origin = remote.get("workflow_run") or {}
    if (remote.get("digest") != artifact["digest"] or remote.get("size_in_bytes") != declared
            or remote.get("expired") or origin.get("id") != evidence.get("run_id")
            or origin.get("head_sha") != evidence.get("commit")):
        raise ValueError("Artifact metadata no longer matches the selected run and commit")
    api = f"{metadata_url}/zip"
    opener = urllib.request.build_opener(_NoRedirect())
    target = None
    try:
        with opener.open(urllib.request.Request(api, headers={"Authorization": f"Bearer {token}"}), timeout=30):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 302:
            target = exc.headers.get("Location")
        exc.close()
    except urllib.error.URLError:
        raise RuntimeError("GitHub artifact URL request failed") from None
    parsed = urlsplit(target or "")
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username is not None or parsed.fragment or parsed.port not in {None, 443}
            or not host.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))):
        raise ValueError("GitHub returned an unsupported artifact download destination")
    try:
        with opener.open(target, timeout=30) as stream:
            data = stream.read(limit + 1)
    except urllib.error.URLError:
        raise RuntimeError("GitHub artifact archive request failed") from None
    if len(data) != declared or "sha256:" + hashlib.sha256(data).hexdigest() != artifact["digest"]:
        raise ValueError("Artifact archive does not match GitHub evidence")
    with output.open("xb") as stream:
        stream.write(data)
    return {"artifact_id": artifact_id, "digest": artifact["digest"], "bytes": len(data)}


def dispatch(config, repository, workflow, ref, inputs, *, exact=False):
    if not config.get("github.allow_dispatch", False):
        raise ValueError("GitHub workflow dispatch is disabled by policy")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository) or repository.endswith(("/.", "/..")):
        raise ValueError("Supply a valid owner/repository for workflow dispatch")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow) or ref not in config.get("github.dispatch_refs", []):
        raise ValueError("Workflow filename or dispatch ref is not authorized")
    token = config.secret("github.token_env")
    if not token:
        raise ValueError("Configure GITHUB_TOKEN with Actions write access for authorized workflow dispatch")
    try:
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"ref": ref, "inputs": inputs}
        if exact:
            headers["X-GitHub-Api-Version"] = "2026-03-10"
            payload["return_run_details"] = True
        response = request_json("POST", f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches",
                                headers=headers, body=payload)
    except HttpError as exc:
        raise RuntimeError(f"GitHub Actions dispatch failed (HTTP {exc.status})") from None
    if exact:
        run = response.get("workflow_run_id") if isinstance(response, dict) else None
        if type(run) is not int or run < 1:
            raise ValueError("GitHub did not confirm a run ID; reconcile the dispatch before retrying")
        return {"run_id": run, "attempt": 1}
    return {"status": "DISPATCH_REQUESTED", "repository": repository, "workflow": workflow, "ref": ref}


def publish_bundle(config, repository, branch, name, archive):
    import base64

    if (not config.get("github.allow_bundle_publication", False)
            or repository != config.get("enterprise.delivery_policy.remote_ci.repository")
            or branch != config.get("github.bundle_branch")
            or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", branch)
            or not re.fullmatch(r"capstone-bundles/[a-f0-9]{64}\.zip", name)):
        raise ValueError("CI bundle publication is outside the allowed scope")
    if len(archive) > 10_000_000:
        raise ValueError("CI bundle exceeds the publication limit")
    token = config.secret("github.token_env")
    if not token:
        raise ValueError("Configure GitHub authentication for bundle publication")
    metadata = request_json("GET", f"https://api.github.com/repos/{repository}",
                            headers={"Authorization": f"Bearer {token}"})
    if metadata.get("private") is not True:
        raise ValueError("Approved evaluation bundles require a private repository")
    result = request_json("PUT", f"https://api.github.com/repos/{repository}/contents/{name}",
                          headers={"Authorization": f"Bearer {token}"}, body={
                              "message": "Publish approved evaluation bundle",
                              "branch": branch, "content": base64.b64encode(archive).decode("ascii"),
                          })
    commit = result["commit"]["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Bundle publication did not return a verified commit identity")
    return commit


def monitor(store, run_id, config, selection):
    """Recoverable read-only polling. Dispatch remains a separately authorized command."""
    import json
    import time
    from contextlib import closing

    from .lifecycle import checkpoint, checkpoint_digest, completed_output

    policy = config.get("enterprise.delivery_policy.remote_ci", {})
    repository, workflow = policy["repository"], policy["workflow"]
    commit, attempt = selection["commit"], selection.get("attempt", 1)
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or type(attempt) is not int or attempt < 1:
        raise ValueError("Invalid CI monitor selection")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow):
        raise ValueError("Invalid CI monitor repository or workflow")
    interval, timeout = policy.get("poll_seconds", 10), policy.get("monitor_timeout", 1800)
    if type(interval) is not int or not 1 <= interval <= 60 or type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("Invalid bounded CI monitor interval or timeout")
    token = config.secret("github.token_env")
    if not token:
        raise ValueError("Configure GitHub Actions read authentication")
    headers = {"Authorization": f"Bearer {token}"}
    base = f"https://api.github.com/repos/{repository}/actions"
    selected = selection.get("run_id")
    if selected is None:
        def discover():
            response = request_json("GET", f"{base}/workflows/{workflow}/runs?head_sha={commit}&per_page=100", headers=headers)
            candidates = [row for row in response["workflow_runs"] if row.get("head_sha") == commit]
            if response.get("total_count", 101) > 100 or len(candidates) != 1:
                raise ValueError("CI discovery is ambiguous; supply the exact workflow run ID")
            return candidates[0]["id"]

        fingerprint = checkpoint_digest([repository, workflow, commit, attempt])
        selected = completed_output("ci-discovery", fingerprint, discover, lambda value: type(value) is int and value > 0)
    if type(selected) is not int or selected < 1:
        raise ValueError("Invalid CI run identity")
    identity = {"repository": repository, "workflow": workflow, "commit": commit, "run_id": selected, "attempt": attempt}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with closing(store._connect()) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS ci_monitors (run_id TEXT, identity TEXT, selection TEXT, "
                           "deadline REAL, state TEXT, PRIMARY KEY(run_id, identity))")
        connection.execute("INSERT OR IGNORE INTO ci_monitors VALUES (?, ?, ?, ?, 'RUNNING')",
                           (run_id, key, json.dumps(identity), time.time() + timeout))
        deadline = connection.execute("SELECT deadline FROM ci_monitors WHERE run_id=? AND identity=?", (run_id, key)).fetchone()[0]
    status = "INTERRUPTED"
    try:
        while True:
            checkpoint()
            if time.time() >= deadline:
                status = "TIMED_OUT"
                raise TimeoutError("CI monitor deadline exhausted")
            response = request_json("GET", f"{base}/runs/{selected}/attempts/{attempt}", headers=headers, timeout=min(20, timeout))
            if (response.get("id") != selected or response.get("run_attempt") != attempt
                    or response.get("head_sha") != commit or str(response.get("path", "")).partition("@")[0] != f".github/workflows/{workflow}"
                    or (response.get("head_repository") or {}).get("full_name") != repository):
                raise ValueError("CI monitor response identity changed")
            if response.get("status") == "completed":
                status = "COMPLETED"
                return collect(config, repository, selected, attempt, commit, workflow)
            pause = time.monotonic() + min(interval, max(0, deadline-time.time()))
            while time.monotonic() < pause:
                checkpoint()
                time.sleep(min(0.25, max(0, pause-time.monotonic())))
    finally:
        with closing(store._connect()) as connection, connection:
            connection.execute("UPDATE ci_monitors SET state=? WHERE run_id=? AND identity=?", (status, run_id, key))
