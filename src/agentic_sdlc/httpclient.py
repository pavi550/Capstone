"""Minimal JSON-over-HTTP helper (stdlib only) used by the Jira, Gerrit and LLM clients."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from queue import Empty, Queue
from threading import BoundedSemaphore, Event, Thread
from time import monotonic
from typing import Any
from urllib.parse import urlparse

_READ_SLOTS = BoundedSemaphore(8)

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
    from .lifecycle import current_stage

    stopped = Event()

    def receive(*, cooperative):
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
                        if stopped.is_set():
                            raise TimeoutError("HTTP observation stopped")
                        if cooperative:
                            checkpoint()
                        if monotonic() >= deadline:
                            raise TimeoutError("HTTP response exceeded the configured deadline")
                        chunk = read_chunk(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks).decode("utf-8")
            if cooperative:
                checkpoint()
        except urllib.error.HTTPError as exc:
            # Authenticated APIs can echo submitted content in an error response.
            message = "request failed" if headers else exc.read(500).decode("utf-8", "replace")
            raise HttpError(exc.code, message) from None
        return raw

    if cancellable and method.upper() in {"GET", "HEAD"} and current_stage.get() is not None:
        result = Queue(maxsize=1)
        while not _READ_SLOTS.acquire(timeout=0.1):
            checkpoint()
            if monotonic() >= deadline:
                raise TimeoutError("HTTP read worker capacity was unavailable")

        def observe():
            try:
                result.put((receive(cooperative=False), None))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                result.put((None, exc))
            finally:
                _READ_SLOTS.release()

        try:
            Thread(target=observe, daemon=True, name="capstone-http-read").start()
        except BaseException:
            _READ_SLOTS.release()
            raise
        try:
            while True:
                checkpoint()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("HTTP response exceeded the configured deadline")
                try:
                    raw, error = result.get(timeout=min(0.1, remaining))
                except Empty:
                    continue
                checkpoint()
                if error is not None:
                    raise error
                break
        finally:
            stopped.set()
    else:
        raw = receive(cooperative=cancellable)
    raw = raw.removeprefix(GERRIT_XSSI_PREFIX)
    return json.loads(raw) if raw.strip() else None
