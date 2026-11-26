"""Minimal JSON-over-HTTP helper (stdlib only) used by the Jira, Gerrit and LLM clients."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from time import monotonic
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 60,
    cancellable: bool = True,
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
    from .lifecycle import checkpoint

    if cancellable:
        checkpoint()
    deadline = monotonic() + timeout
    try:
        opener = urllib.request.build_opener(_NoRedirect()) if headers else urllib.request
        open_url = opener.open if headers else opener.urlopen
        with open_url(req, timeout=timeout) as resp:
            read_chunk = getattr(resp, "read1", None)
            if read_chunk is None:
                raw = resp.read().decode("utf-8")
            else:
                chunks = []
                while True:
                    if cancellable:
                        checkpoint()
                    if monotonic() >= deadline:
                        raise TimeoutError("HTTP response exceeded the configured deadline")
                    chunk = read_chunk(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks).decode("utf-8")
        if cancellable:
            checkpoint()
    except urllib.error.HTTPError as exc:
        # Authenticated APIs can echo submitted content in an error response.
        message = "request failed" if headers else exc.read(500).decode("utf-8", "replace")
        raise HttpError(exc.code, message) from None
    raw = raw.removeprefix(GERRIT_XSSI_PREFIX)
    return json.loads(raw) if raw.strip() else None
