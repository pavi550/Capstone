"""Configuration loading: packaged `sdlc.yaml` defaults deep-merged with user overrides."""

from __future__ import annotations

import copy
import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict | None = None) -> Config:
        text = resources.files("agentic_sdlc").joinpath("sdlc.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if path:
            data = _deep_merge(data, yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
        if overrides:
            data = _deep_merge(data, overrides)
        return cls(data)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def secret(self, dotted_env_key: str) -> str | None:
        """Resolve a credential from the env var named at `dotted_env_key` (e.g. `jira.token_env`)."""
        name = self.get(dotted_env_key)
        return os.environ.get(name) if name else None
