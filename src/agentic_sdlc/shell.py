"""Single place where external commands are executed (never through a shell)."""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


def split(cmd: str | list[str]) -> list[str]:
    if isinstance(cmd, list):
        return [str(c) for c in cmd]
    return shlex.split(cmd, posix=os.name != "nt")


def run(
    cmd: str | list[str], cwd: Path | str | None = None, timeout: int = 900
) -> CommandResult:
    args = split(cmd)
    try:
        proc = subprocess.run(  # nosec B603
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        return CommandResult(args, 127, "", f"command not found: {exc}")
    except subprocess.TimeoutExpired:
        return CommandResult(args, 124, "", f"timed out after {timeout}s")
    return CommandResult(args, proc.returncode, proc.stdout or "", proc.stderr or "")
