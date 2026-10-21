"""Minimal JSON-over-HTTP helper (stdlib only) used by the Jira, Gerrit and LLM clients."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

GERRIT_XSSI_PREFIX = ")]}'"


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def basic_auth(user: str, secret: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 60,
) -> Any:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read().decode("utf-8", "replace")[:500]) from exc
    raw = raw.removeprefix(GERRIT_XSSI_PREFIX)
    return json.loads(raw) if raw.strip() else None
