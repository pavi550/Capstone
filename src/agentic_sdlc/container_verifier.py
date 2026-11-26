"""Run trusted-policy checks in local, network-disabled Linux Docker containers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .development import _manifest, _snapshot
from .lifecycle import CleanupUnresolved, StageCancelled, checkpoint

CHECK_IDS = {"acceptance", "generated", "regression", "quality", "security", "definition_of_done", "dev"}


def _environment():
    return {
        name: os.environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "PATHEXT", "TEMP", "TMP")
        if name in os.environ
    }


def _mount(source: Path, destination: str) -> str:
    if any(character in str(source) for character in ',\r\n"'):
        raise ValueError("Verifier paths cannot contain commas, quotes or line breaks")
    return f"type=bind,src={source},dst={destination},readonly,bind-recursive=disabled"


def _digest_stream(stream):
    with stream:
        digest = hashlib.sha256()
        size = 0
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
        return {"sha256": digest.hexdigest(), "bytes": size}


def _capture(command, *, env, timeout, cleanup):
    with subprocess.Popen(  # nosec B603 - argument vector built for the trusted local Docker CLI
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        start_new_session=os.name == "posix",
    ) as process, ThreadPoolExecutor(max_workers=2) as pool:
        stdout = pool.submit(_digest_stream, process.stdout)
        stderr = pool.submit(_digest_stream, process.stderr)
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    process.wait(min(remaining, 0.25))
                except subprocess.TimeoutExpired:
                    continue
            exit_code = process.returncode
        except (subprocess.TimeoutExpired, StageCancelled, KeyboardInterrupt) as exc:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            # Remove only the uniquely named container created for this check.
            try:
                subprocess.run(cleanup, env=env, capture_output=True, timeout=15, check=False)  # nosec B603
            except (OSError, subprocess.TimeoutExpired):
                pass
            if not isinstance(exc, subprocess.TimeoutExpired):
                raise
            exit_code = None
    return exit_code, stdout.result(), stderr.result()


def _container_records(docker, env, host, *, register=None):
    """Track owned verifier containers and reconcile them through the same daemon."""
    from contextlib import closing

    from .lifecycle import current_stage

    context = current_stage.get()
    if context is None:
        return
    store, run_id, stage, attempt = context
    if register:
        with closing(store._connect()) as connection, connection:
            connection.execute("INSERT INTO lifecycle_containers VALUES (?, ?, ?, ?, ?, 'PENDING')",
                               (register, run_id, stage, attempt, host))
        return
    with closing(store._connect()) as connection:
        pending = connection.execute("SELECT name FROM lifecycle_containers WHERE run_id=? AND host=? "
                                     "AND cleanup_state!='VERIFIED_ABSENT'", (run_id, host)).fetchall()
    for (name,) in pending:
        if not re.fullmatch(r"capstone-verify-[a-f0-9]{32}", name):
            raise ValueError("Invalid owned verifier identity")
        command = [*docker, "ps", "-aq", "--no-trunc", "--filter", f"label=capstone.verifier={name}",
                   "--filter", f"name=^/{name}$"]
        before = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15, check=True)
        container = before.stdout.strip()
        if container:
            if not re.fullmatch(r"[a-f0-9]{64}", container):
                raise ValueError("Ambiguous owned verifier container")
            subprocess.run([*docker, "rm", "-f", container], env=env, capture_output=True, timeout=15, check=False)
        after = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15, check=True)
        if after.stdout.strip():
            raise ValueError("Verifier container cleanup remains unresolved")
        with closing(store._connect()) as connection, connection:
            connection.execute("UPDATE lifecycle_containers SET cleanup_state='VERIFIED_ABSENT' WHERE name=?", (name,))


class DockerVerifier:
    """The operator supplies a trusted pinned local image, commands, and acceptance tests."""

    def __init__(self, cfg):
        self.image = cfg.get("enterprise.container.image")
        self.checks = cfg.get("enterprise.container.commands")
        self.timeout = cfg.get("enterprise.container.timeout", 90)
        if not isinstance(self.image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image):
            raise ValueError("Configure enterprise.container.image with a trusted local Docker image ID")
        if not isinstance(self.checks, dict) or set(self.checks) != CHECK_IDS:
            raise ValueError("Configure acceptance, generated, regression, quality, security, definition_of_done and dev commands")
        for argv in self.checks.values():
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv)
                    or not argv[0].startswith("/")):
                raise ValueError("Verifier commands require a nonempty argv with an absolute container executable")
        if not any(arg == "/acceptance" or arg.startswith("/acceptance/") for arg in self.checks["acceptance"][1:]):
            raise ValueError("The acceptance command must explicitly select trusted inputs under /acceptance")
        if type(self.timeout) is not int or not 1 <= self.timeout <= 600:
            raise ValueError("Container timeout must be between 1 and 600 seconds")
        self.executable = shutil.which("docker")
        if not self.executable:
            raise ValueError("A local Docker CLI is required for actual verification")
        self.host = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"

    def run(self, integrated: Path, acceptance: Path, identity: dict, expected: dict) -> list[dict]:
        acceptance = acceptance.resolve()
        if (acceptance.is_relative_to(integrated) or integrated.is_relative_to(acceptance)
                or not acceptance.is_dir()):
            raise ValueError("Provide an independent acceptance directory outside the generated workspace")
        return self._run(integrated, acceptance, identity, expected, sorted(CHECK_IDS))

    def run_dev(self, integrated: Path, identity: dict, expected: dict) -> dict:
        return self._run(integrated, None, identity, expected, ("dev",))[0]

    def _run(self, integrated, acceptance, identity, expected, checks):
        env = _environment()
        with tempfile.TemporaryDirectory(prefix="capstone-verifier-") as folder:
            private = Path(folder)
            docker = [self.executable, "--config", str(private), "--host", self.host]
            try:
                info = subprocess.run(  # nosec B603 - fixed read-only Docker server probe
                    [*docker, "version", "--format", "{{.Server.Os}}"],
                    env=env, capture_output=True, timeout=10, check=False, text=True,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Local Docker engine is unavailable; no code was executed") from None
            if info.returncode != 0 or info.stdout.strip() != "linux":
                raise ValueError("Local Linux Docker engine is unavailable; no code was executed")
            _container_records(docker, env, self.host)
            workspace, trusted = private / "artifact", private / "acceptance"
            _snapshot(integrated, workspace)
            if _manifest(workspace) != expected:
                raise ValueError("The verifier snapshot differs from the approved artifact")
            acceptance_sha256 = None
            if acceptance is not None:
                _snapshot(acceptance, trusted)
                if not any(trusted.iterdir()):
                    raise ValueError("The trusted acceptance directory is empty")
                acceptance_sha256 = hashlib.sha256(
                    repr(sorted(_manifest(trusted).items())).encode(),
                ).hexdigest()
            return [self._check(docker, env, workspace, trusted, acceptance_sha256, identity, check)
                    for check in checks]

    def _check(self, docker, env, workspace, trusted, acceptance_sha256, identity, check):
        name = f"capstone-verify-{uuid4().hex}"
        executable, *arguments = self.checks[check]
        command = [
            *docker, "run", "--name", name, "--label", f"capstone.verifier={name}", "--rm", "--pull", "never", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "512m", "--cpus", "1", "--user", "65532:65532",
            "--no-healthcheck", "--log-driver", "none",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--env", "PYTHONPATH=/artifact", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", _mount(workspace, "/artifact"), "--workdir", "/artifact",
        ]
        if check == "acceptance":
            command.extend(("--mount", _mount(trusted, "/acceptance")))
        command.extend(("--entrypoint", executable, self.image, *arguments))
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        _container_records(docker, env, self.host, register=name)
        try:
            exit_code, stdout, stderr = _capture(
                command, env=env, timeout=self.timeout, cleanup=[*docker, "rm", "-f", name],
            )
        finally:
            try:
                _container_records(docker, env, self.host)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise CleanupUnresolved("Verifier container termination could not be confirmed") from exc
        return {
            **identity, "check_id": check, "command_id": check, "execution_mode": "LOCAL_ACTUAL",
            "image_id": self.image, "acceptance_sha256": acceptance_sha256 if check == "acceptance" else None,
            "command_sha256": hashlib.sha256(json.dumps([executable, *arguments]).encode()).hexdigest(),
            "test_counts": None, "coverage": None,
            "started_at": started_at, "completed_at": datetime.now(UTC).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "exit_code": exit_code,
            "result": ("PASSED" if exit_code == 0 else "TIMED_OUT" if exit_code is None
                       else "INFRASTRUCTURE_FAILED" if exit_code in {125, 126, 127} else "FAILED"),
            "stdout": stdout, "stderr": stderr, "evaluator_version": "docker-v1",
        }
