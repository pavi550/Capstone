"""Local MCP configuration. Values are loaded on demand without exporting secrets."""

import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
JIRA_VARIABLES = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
GITHUB_VARIABLES = ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_BRANCH")


class ConfigurationError(RuntimeError):
    """An actionable configuration error containing no user-supplied values."""


def settings(path: Path | None = None, environ: Mapping[str, str] | None = None):
    try:
        local = dotenv_values(
            path if path is not None else ENV_FILE, encoding="utf-8-sig", interpolate=False,
        )
    except (OSError, UnicodeError):
        raise ConfigurationError("Could not read the repository .env file as UTF-8.") from None
    return {
        **{key: value or "" for key, value in local.items()},
        **(os.environ if environ is None else environ),
    }


def required(values, *names):
    missing = [name for name in names if not values.get(name, "").strip()]
    if missing:
        raise ConfigurationError("Missing required configuration: " + ", ".join(missing))
    return tuple(values[name].strip() for name in names)


def endpoint(value, name, *, allow_loopback=False):
    try:
        url = urlsplit(value)
        port = url.port
        allowed_scheme = url.scheme == "https" or (
            allow_loopback and url.scheme == "http"
            and url.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (allowed_scheme and url.hostname and not url.username and url.password is None
                and not re.search(r"[\s\\?#\x00-\x1f\x7f@]", value)
                and (port is None or port > 0)):
            return value.rstrip("/")
    except ValueError:
        pass
    protocols = "HTTPS or loopback HTTP" if allow_loopback else "HTTPS"
    raise ConfigurationError(f"{name} must use {protocols} without credentials, query or fragment.")


def number(value, name, minimum, maximum):
    try:
        result = float(value)
    except ValueError:
        result = math.nan
    if not minimum <= result <= maximum:
        raise ConfigurationError(f"{name} must be a number between {minimum} and {maximum}.")
    return result


def jira_settings(values):
    url, email, token = required(values, *JIRA_VARIABLES)
    return endpoint(url, "JIRA_BASE_URL"), email, token


def github_settings(values):
    token, = required(values, "GITHUB_TOKEN")
    owner = values.get("GITHUB_OWNER", "pavi550").strip()
    repo = values.get("GITHUB_REPO", "Capstone").strip()
    branch = values.get("GITHUB_BRANCH", "main").strip()
    if "/" in repo:
        owner, _, repo = repo.partition("/")
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in {".", ".."}
               for part in (owner, repo)) or not branch:
        raise ConfigurationError("Check GITHUB_OWNER, GITHUB_REPO and GITHUB_BRANCH.")
    return token, owner, repo, branch


def confluence_settings(values):
    url, email, token = required(
        values, "CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
    )
    url = endpoint(url, "CONFLUENCE_BASE_URL")
    if urlsplit(url).path not in {"", "/wiki"}:
        raise ConfigurationError("CONFLUENCE_BASE_URL must be a Confluence Cloud site URL or end in /wiki.")
    return url if url.endswith("/wiki") else f"{url}/wiki", email, token
