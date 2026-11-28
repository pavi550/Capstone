"""Configuration loading: packaged `sdlc.yaml` defaults deep-merged with user overrides."""

from __future__ import annotations

import copy
import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, data: dict[str, Any], local_env: dict[str, str | None] | None = None):
        self.data = data
        self._local_env = local_env or {}

    @classmethod
    def load(
        cls, path: str | Path | None = None, overrides: dict | None = None, *,
        enterprise_intake: bool = False, local_model: bool = False,
    ) -> Config:
        text = resources.files("agentic_sdlc").joinpath("sdlc.yaml").read_text(encoding="utf-8")
        try:
            env_file = os.environ.get("CAPSTONE_ENV_FILE") or Path.cwd() / ".env"
            local = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
        except (OSError, UnicodeError):
            raise ValueError("Could not read the project .env as UTF-8") from None
        values = {**local, **os.environ}
        settings: dict[str, Any] = {}
        if values.get("JIRA_BASE_URL"):
            jira = {
                "enabled": enterprise_intake,
                "base_url": values["JIRA_BASE_URL"],
                "search_path": "/rest/api/3/search/jql",
            }
            if values.get("JIRA_JQL"):
                jira["jql"] = values["JIRA_JQL"]
            settings["jira"] = jira
        if values.get("JIRA_EMAIL"):
            settings.setdefault("jira", {})["user_env"] = "JIRA_EMAIL"
        if values.get("JIRA_API_TOKEN"):
            # Store the environment variable name. The credential stays in the environment.
            settings.setdefault("jira", {})["token_env"] = "JIRA_API_TOKEN"  # nosec B105
        if values.get("CONFLUENCE_BASE_URL"):
            settings["confluence"] = {"base_url": values["CONFLUENCE_BASE_URL"]}
        if local_model and values.get("LLM_PROVIDER") == "openai_compatible" and values.get("LLM_BASE_URL"):
            settings["llm"] = {"provider": "openai", "base_url": values["LLM_BASE_URL"], "api_key_env": "LLM_API_KEY"}
            if values.get("LLM_MODEL"):
                settings["llm"]["model"] = values["LLM_MODEL"]
        data = _deep_merge(yaml.safe_load(text), settings)
        if path:
            data = _deep_merge(data, yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
        if overrides:
            data = _deep_merge(data, overrides)
        return cls(data, local)

    def with_overrides(self, overrides: dict) -> Config:
        return Config(_deep_merge(self.data, overrides), self._local_env)

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
        return os.environ.get(name, self._local_env.get(name)) if name else None
