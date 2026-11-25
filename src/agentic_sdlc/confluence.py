"""Read Confluence Cloud pages by configured ID and record source provenance."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from .config import Config
from .httpclient import HttpError, basic_auth, request_json


class ConfluenceClient:
    def __init__(self, cfg: Config):
        base = (cfg.get("confluence.base_url") or "").rstrip("/")
        parsed = urlsplit(base)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment or parsed.path not in {"", "/wiki"}):
            raise ValueError("confluence.base_url must be an HTTPS Cloud site or its /wiki URL")
        email, token = cfg.secret("confluence.user_env"), cfg.secret("confluence.token_env")
        if not email or not token:
            raise ValueError("Configure the Confluence account email and API token environment variables")
        self.base = base if parsed.path == "/wiki" else f"{base}/wiki"
        self.headers = basic_auth(email, token)
        self.max_chars = cfg.get("confluence.max_chars", 10000)
        if type(self.max_chars) is not int or not 1 <= self.max_chars <= 100000:
            raise ValueError("confluence.max_chars must be between 1 and 100000")

    def read_page(self, page_id: str) -> dict:
        if not re.fullmatch(r"[0-9]+", page_id):
            raise ValueError("Confluence page ID must contain only digits")
        try:
            page = request_json(
                "GET", f"{self.base}/api/v2/pages/{page_id}?body-format=storage",
                headers=self.headers,
            )
        except HttpError as exc:
            raise RuntimeError(f"Confluence page read failed (HTTP {exc.status})") from None
        try:
            version = page["version"]["number"]
            body = page["body"]["storage"]["value"]
            if str(page["id"]) != page_id or type(version) is not int:
                raise ValueError
            digest = hashlib.sha256(body.encode()).hexdigest()
            return {
                "id": page_id, "version": version, "title": page["title"],
                "url": f"{self.base}/pages/viewpage.action?pageId={page_id}",
                "sha256": digest, "truncated": len(body) > self.max_chars,
                "untrusted_storage": body[:self.max_chars],
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            raise RuntimeError("Confluence returned invalid page provenance or content") from None
