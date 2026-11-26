"""Client for a trusted DEV broker with owned, idempotently addressed deployments."""

import hashlib
import json
import time
from urllib.parse import urlsplit

from .httpclient import HttpError, request_json
from .lifecycle import CleanupUnresolved, StageCancelled, checkpoint, record_reconciliation


def run(config, binding, *, create):
    policy = config.get("enterprise.remote_dev", {})
    endpoint = policy.get("endpoint", "").rstrip("/")
    parsed = urlsplit(endpoint)
    environment = policy.get("environment", "")
    if (not policy.get("enabled") or policy.get("tier") != "DEV" or not environment.startswith("dev-")
            or environment not in policy.get("allowed_environments", []) or parsed.scheme != "https"
            or not parsed.hostname or parsed.username is not None or parsed.query or parsed.fragment):
        raise ValueError("Configure an authorized HTTPS DEV-only broker and environment")
    token = config.secret("enterprise.remote_dev.token_env")
    if not token:
        raise ValueError("Configure the DEV broker credential")
    timeout = policy.get("timeout", 120)
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise ValueError("DEV broker timeout must be between 1 and 1800 seconds")
    identity = {**binding, "environment": environment, "tier": "DEV"}
    deployment = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    url = f"{endpoint}/deployments/{deployment}"
    headers = {"Authorization": f"Bearer {token}"}

    def request(method, body=None):
        try:
            if method == "DELETE":
                return request_json(method, url, body=body, headers=headers, timeout=min(timeout, 20), cancellable=False)
            return request_json(method, url, body=body, headers=headers, timeout=min(timeout, 20))
        except HttpError as exc:
            raise RuntimeError(f"DEV broker {method} failed (HTTP {exc.status})") from None

    try:
        checkpoint()
        state = request("PUT", identity) if create else request("GET")
        deadline = time.monotonic() + timeout
        while True:
            if state.get("deployment_id") != deployment or state.get("binding") != identity:
                raise ValueError("DEV broker returned evidence for a different deployment or artifact")
            status = state.get("status")
            if status in {"PASSED", "FAILED", "BLOCKED", "CANCELLED"}:
                checks = state.get("checks", {})
                required = {"prepare", "build", "deploy", "start", "health", "smoke", "cleanup"}
                if status == "PASSED" and (not required <= checks.keys() or not all(checks[key] == "PASSED" for key in required)):
                    raise ValueError("DEV broker reported success without mandatory check and cleanup evidence")
                if policy.get("receipt_key_env"):
                    import hmac
                    key = config.secret("enterprise.remote_dev.receipt_key_env")
                    payload = {name: value for name, value in state.items() if name != "mac"}
                    if not key or len(key.encode()) < 32 or not hmac.compare_digest(
                        hmac.new(key.encode(), json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest(),
                        str(state.get("mac", "")),
                    ):
                        raise ValueError("DEV broker receipt signature is invalid")
                if not create:
                    record_reconciliation("DEV_BROKER_TERMINAL", status)
                return state
            if status not in {"PENDING", "RUNNING", "CANCELLING"}:
                raise ValueError("DEV broker returned an unsupported state")
            checkpoint()
            if time.monotonic() >= deadline:
                if create:
                    request("DELETE")
                record_reconciliation("DEV_BROKER_TIMEOUT", "UNCERTAIN")
                raise CleanupUnresolved("Remote DEV termination is unconfirmed after timeout")
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            state = request("GET")
    except StageCancelled as exc:
        if create:
            try:
                stopped = request("DELETE")
            except (OSError, RuntimeError, ValueError):
                stopped = None
            if (isinstance(stopped, dict) and stopped.get("deployment_id") == deployment
                    and stopped.get("binding") == identity and stopped.get("status") == "CANCELLED"
                    and stopped.get("checks", {}).get("cleanup") == "PASSED"):
                record_reconciliation("DEV_BROKER_CLEANUP_CONFIRMED", "CANCELLED")
                raise
        record_reconciliation("DEV_BROKER_CLEANUP_UNCONFIRMED", "UNCERTAIN")
        raise CleanupUnresolved("Remote DEV cleanup remains unconfirmed") from exc


class Broker:
    """Durable broker protocol. Artifacts and DEV policy are provisioned by the operator."""

    def __init__(self, store, config):
        from contextlib import closing
        self.store, self.config = store, config
        with closing(store._connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS dev_deployments (deployment_id TEXT PRIMARY KEY, "
                               "run_id TEXT NOT NULL, binding TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT)")

    def request(self, method, deployment, binding=None):
        import re
        from contextlib import closing

        if not re.fullmatch(r"[a-f0-9]{64}", deployment):
            raise ValueError("Invalid deployment identity")
        policy = self.config.get("enterprise.remote_dev.server", {})
        if not policy.get("enabled"):
            raise ValueError("DEV broker is disabled")
        if method == "PUT":
            environment = binding.get("environment", "")
            if (binding.get("tier") != "DEV" or not environment.startswith("dev-")
                    or environment not in policy.get("environments", {})):
                raise ValueError("Environment is not an authorized DEV environment")
            digest = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
            if digest != deployment:
                raise ValueError("Idempotency key differs from request binding")
            for field in ("manifest_sha256", "spec_sha256", "approval_sha256" if "approval_sha256" in binding else "review_sha256"):
                if not re.fullmatch(r"[a-f0-9]{64}", str(binding.get(field, ""))):
                    raise ValueError("Missing artifact or approval binding")
        with closing(self.store._connect()) as connection:
            existing = connection.execute("SELECT * FROM dev_deployments WHERE deployment_id=?", (deployment,)).fetchone()
        if existing is None:
            if method != "PUT":
                raise KeyError("Unknown deployment")
            run_id = self.store.begin_intake("DEV-" + deployment[:12])
            with closing(self.store._connect()) as connection, connection:
                connection.execute("INSERT OR IGNORE INTO dev_deployments VALUES (?, ?, ?, 'PENDING', NULL)",
                                   (deployment, run_id, json.dumps(binding, sort_keys=True)))
                existing = connection.execute("SELECT * FROM dev_deployments WHERE deployment_id=?", (deployment,)).fetchone()
        if method == "PUT" and json.loads(existing["binding"]) != binding:
            raise ValueError("Deployment identity conflict")
        if method == "DELETE" and existing["state"] not in {"PASSED", "FAILED", "BLOCKED", "CANCELLED"}:
            self.store.cancel(existing["run_id"])
            with closing(self.store._connect()) as connection, connection:
                state = "CANCELLED" if existing["state"] == "PENDING" else "CANCELLING"
                if existing["state"] in {"PENDING", "RUNNING", "INTERRUPTED"}:
                    connection.execute("UPDATE dev_deployments SET state=? WHERE deployment_id=?", (state, deployment))
        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT * FROM dev_deployments WHERE deployment_id=?", (deployment,)).fetchone()
        result = {"deployment_id": deployment, "binding": json.loads(row["binding"]), "status": row["state"], "checks": {}}
        if row["receipt"]:
            result.update(json.loads(row["receipt"]))
        return result

    def application(self, environ, start_response):
        import hmac
        try:
            key = self.config.secret("enterprise.remote_dev.server.token_env")
            supplied = environ.get("HTTP_AUTHORIZATION", "")
            if not key or len(key.encode()) < 32 or not hmac.compare_digest(supplied, "Bearer " + key):
                start_response("401 Unauthorized", [("Content-Type", "application/json")])
                return [b'{"error":"Unauthorized"}']
            path = environ.get("PATH_INFO", "")
            if not path.startswith("/deployments/"):
                raise KeyError("Unknown route")
            method = environ.get("REQUEST_METHOD")
            if method not in {"GET", "PUT", "DELETE"}:
                raise ValueError("Unsupported operation")
            body = None
            if method == "PUT":
                length = int(environ.get("CONTENT_LENGTH") or 0)
                if not 0 < length <= 16384:
                    raise ValueError("Invalid request size")
                data = environ["wsgi.input"].read(length)
                if len(data) != length:
                    raise ValueError("Incomplete request")
                body = json.loads(data)
            result = self.request(method, path.removeprefix("/deployments/"), body)
        except (KeyError, ValueError, TypeError, UnicodeError):
            start_response("400 Bad Request", [("Content-Type", "application/json")])
            return [b'{"error":"Rejected DEV request"}']
        start_response("200 OK", [("Content-Type", "application/json")])
        return [json.dumps(result, allow_nan=False).encode()]

    def work_next(self):
        """Consume at most one pending deployment from the durable broker queue."""
        from contextlib import closing

        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT deployment_id FROM dev_deployments WHERE state='PENDING' ORDER BY rowid LIMIT 1",
            ).fetchone()
        return self.work(row[0]) if row else {"status": "IDLE"}

    def work(self, deployment):
        import hmac
        from contextlib import closing
        from pathlib import Path

        from .container_verifier import DockerVerifier
        from .development import verify_artifact
        from .lifecycle import execute

        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT * FROM dev_deployments WHERE deployment_id=?", (deployment,)).fetchone()
        if row is None:
            raise ValueError("Unknown deployment")
        if row["receipt"] or row["state"] == "CANCELLED":
            return self.request("GET", deployment)
        if row["state"] in {"RUNNING", "CANCELLING", "INTERRUPTED"}:
            raise ValueError("Interrupted deployment requires resource reconciliation before any new execution")
        binding = json.loads(row["binding"])
        policy = self.config.get("enterprise.remote_dev.server", {})
        environment = policy.get("environments", {}).get(binding["environment"])
        if not policy.get("enabled") or binding["tier"] != "DEV" or not environment:
            raise ValueError("DEV worker environment is disabled")
        signing_key = self.config.secret("enterprise.remote_dev.server.receipt_key_env")
        if not signing_key or len(signing_key.encode()) < 32:
            raise ValueError("Configure the independent broker receipt signing key")
        artifact_path = policy.get("artifacts", {}).get(binding["manifest_sha256"])
        if not artifact_path:
            raise ValueError("Artifact is not provisioned in the trusted broker catalog")
        root = Path(artifact_path)
        verify_artifact({"artifact_dir": str(root), "manifest_sha256": binding["manifest_sha256"]})
        manifest = json.loads((root / "manifest.json").read_text())
        cfg = self.config.with_overrides(environment.get("configuration", {}))
        plan = cfg.get("enterprise.container.commands.dev", [])
        if len(plan) < 4 or plan[-2] != "/opt/capstone/dev_harness.py":
            raise ValueError("Remote DEV requires the trusted application harness")
        harness = json.loads(plan[-1])
        if not harness.get("build") or not harness.get("start") or not harness.get("health") or not harness.get("smoke"):
            raise ValueError("Remote DEV requires build, start, health and smoke operations")

        def operation():
            with closing(self.store._connect()) as connection, connection:
                connection.execute("UPDATE dev_deployments SET state='RUNNING' WHERE deployment_id=?", (deployment,))
            result = None
            try:
                adapter = environment.get("adapter")
                if adapter == "docker":
                    evidence = DockerVerifier(cfg).run_dev(root / "integrated", binding, manifest["integrated"])
                elif adapter == "kubernetes":
                    evidence = _kubernetes_dev(cfg, environment, deployment, root, binding)
                else:
                    raise ValueError("Unsupported DEV adapter")
                passed = evidence.get("result") == "PASSED"
                result = {"status": "PASSED" if passed else "FAILED", "evidence": evidence,
                          "checks": {name: "PASSED" if passed else "NOT_EVALUATED" for name in
                                     ("prepare", "build", "deploy", "start", "health", "smoke", "cleanup")}}
            except BaseException as exc:
                result = {"status": "CANCELLED" if isinstance(exc, StageCancelled) else "BLOCKED",
                          "checks": {}, "failure": type(exc).__name__}
                raise
            finally:
                if result is not None:
                    payload = {"deployment_id": deployment, "binding": binding, **result}
                    result["mac"] = hmac.new(signing_key.encode(), json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()
                    with closing(self.store._connect()) as connection, connection:
                        connection.execute("UPDATE dev_deployments SET state=?, receipt=? WHERE deployment_id=?",
                                           (result["status"], json.dumps(result), deployment))
            return result
        execute(self.store, row["run_id"], "remote-dev", operation)
        return self.request("GET", deployment)


def _kubernetes_dev(config, environment, deployment, root, binding):
    """Run the existing DEV harness in a restricted, owned Kubernetes Job."""
    import base64
    import re
    import shutil
    import subprocess

    from .container_verifier import _environment
    from .development import _manifest

    namespace, context = environment.get("namespace", ""), environment.get("context", "")
    image = environment.get("image", "")
    executable = shutil.which("kubectl")
    if (not executable or not re.fullmatch(r"dev-[a-z0-9-]{1,50}", namespace)
            or not isinstance(context, str) or not context or context.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}", image)):
        raise ValueError("Configure a kubectl context, DEV-only namespace and immutable trusted harness image")
    timeout = config.get("enterprise.container.timeout", 90)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("Invalid Kubernetes DEV deadline")
    kubeconfig = environment.get("kubeconfig")
    if not isinstance(kubeconfig, str) or not kubeconfig:
        raise ValueError("Configure an operator-owned least-privilege kubeconfig")
    argv = [executable, "--kubeconfig", kubeconfig, "--context", context, "--namespace", namespace, "--request-timeout=10s"]
    env = _environment()

    def call(arguments, document=None):
        result = subprocess.run([*argv, *arguments], input=json.dumps(document) if document else None,
                                env=env, text=True, capture_output=True, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError("Kubernetes DEV operation failed; operator reconciliation may be required")
        return json.loads(result.stdout) if result.stdout.strip() else None

    policies = call(["get", "networkpolicies", "-o", "json"])["items"]
    if len(policies) != 1:
        raise ValueError("DEV namespace needs exactly one deny-all network policy")
    network = policies[0]["spec"]
    if (network.get("podSelector") != {} or network.get("ingress") or network.get("egress")
            or set(network.get("policyTypes", [])) != {"Ingress", "Egress"}):
        raise ValueError("DEV namespace must deny application ingress and egress")
    files = _manifest(root / "integrated")
    data, items, total = {}, [], 0
    for index, name in enumerate(files):
        content = (root / "integrated" / name).read_bytes()
        total += len(content)
        if total > 500_000:
            raise ValueError("Kubernetes inline artifact limit exceeded; provision a supported smaller artifact")
        key = f"file-{index}"
        data[key] = base64.b64encode(content).decode()
        items.append({"key": key, "path": name})
    name = "capstone-" + deployment[:32]
    metadata = {"name": name, "namespace": namespace, "labels": {"capstone-owner": deployment}}
    configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata, "immutable": True, "binaryData": data}
    command = config.get("enterprise.container.commands.dev")
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": metadata, "spec": {
        "backoffLimit": 0, "activeDeadlineSeconds": timeout, "ttlSecondsAfterFinished": 600,
        "template": {"metadata": {"labels": {"capstone-owner": deployment}}, "spec": {
            "restartPolicy": "Never", "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "dev", "image": image, "command": command, "workingDir": "/artifact",
                "env": [{"name": "PYTHONPATH", "value": "/artifact"}, {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                "volumeMounts": [{"name": "artifact", "mountPath": "/artifact", "readOnly": True},
                                 {"name": "scratch", "mountPath": "/tmp"}]}],
            "volumes": [{"name": "artifact", "configMap": {"name": name, "items": items}},
                        {"name": "scratch", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]}}}}

    def owned(kind):
        observed = call(["get", kind, name, "--ignore-not-found", "-o", "json"])
        if observed and observed["metadata"].get("labels", {}).get("capstone-owner") != deployment:
            raise ValueError("Kubernetes resource ownership conflict")
        return observed

    outcome = "FAILED"
    started = time.monotonic()
    try:
        for kind, resource in (("configmap", configmap), ("job", job)):
            checkpoint()
            expected = hashlib.sha256(json.dumps(resource, sort_keys=True).encode()).hexdigest()
            existing = owned(kind)
            if existing:
                if existing["metadata"].get("annotations", {}).get("capstone-spec") != expected:
                    raise ValueError("Recovered Kubernetes resource specification differs")
            else:
                resource["metadata"] = {**resource["metadata"], "annotations": {"capstone-spec": expected}}
                call(["create", "-f", "-", "-o", "json"], resource)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            checkpoint()
            state = owned("job") or {}
            status = state.get("status", {})
            if status.get("succeeded") == 1:
                outcome = "PASSED"
                break
            if status.get("failed"):
                break
            time.sleep(0.25)
        else:
            outcome = "TIMED_OUT"
    finally:
        for kind in ("job", "configmap"):
            if owned(kind):
                call(["delete", kind, name, "--wait=true", "--timeout=10s", "-o", "json"])
    return {**binding, "result": outcome, "adapter": "kubernetes", "namespace": namespace,
            "resource": name, "image": image, "duration_ms": (time.monotonic()-started)*1000,
            "cleanup": "COMPLETED"}


def serve_broker(store, config):
    from wsgiref.simple_server import make_server
    policy = config.get("enterprise.remote_dev.server", {})
    if not policy.get("enabled"):
        raise ValueError("DEV broker server is disabled")
    port = policy.get("port", 8788)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid broker loopback port")
    broker = Broker(store, config)
    with make_server("127.0.0.1", port, broker.application) as server:
        server.serve_forever(poll_interval=0.5)
