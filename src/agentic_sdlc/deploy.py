"""12. Deployment: promote through `deploy.environments` (Dev -> QA -> Staging -> Prod),
with a HITL approval per environment. Stops promoting at the first rejection or failure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import shell
from .config import Config
from .hitl import HITL


def deploy(cfg: Config, hitl: HITL, repo: Path | None) -> list[dict[str, Any]]:
    template = cfg.get("deploy.command")
    dry_run = cfg.get("deploy.dry_run", True) or not template
    results = []
    for env in cfg.get("deploy.environments", []):
        if not hitl.approve(f"deploy:{env}", f"promote to {env}"):
            results.append({"env": env, "status": "rejected"})
            break
        if dry_run:
            results.append({"env": env, "status": "dry-run",
                            "command": template.format(env=env) if template else None})
            continue
        res = shell.run(template.format(env=env), cwd=repo)
        results.append({"env": env, "status": "deployed" if res.ok else "failed",
                        "returncode": res.returncode})
        if not res.ok:
            break
    return results
