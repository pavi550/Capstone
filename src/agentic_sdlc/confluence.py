"""Read Confluence Cloud pages by configured ID and record source provenance."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

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
        self.origin = urlsplit(self.base)
        self.cfg = cfg
        self.max_chars = cfg.get("confluence.max_chars", 10000)
        if type(self.max_chars) is not int or not 1 <= self.max_chars <= 100000:
            raise ValueError("confluence.max_chars must be between 1 and 100000")

    def _get(self, url, operation):
        try:
            result = request_json("GET", url, headers=self.headers)
        except HttpError as exc:
            raise RuntimeError(f"Confluence {operation} failed (HTTP {exc.status})") from None
        if not isinstance(result, dict):
            raise TypeError(f"Confluence returned an invalid {operation} response")
        return result

    def _list(self, path, operation, params=None, *, limit=1000):
        url = f"{self.base}{path}?{urlencode({'limit': min(limit, 100), **(params or {})})}"
        seen, rows = set(), []
        while url and len(rows) < limit:
            parsed = urlsplit(url)
            if (parsed.scheme != self.origin.scheme or parsed.netloc != self.origin.netloc
                    or parsed.path != f"/wiki{path}" or parsed.fragment or url in seen or len(seen) >= 100):
                raise RuntimeError(f"Confluence returned invalid {operation} pagination")
            seen.add(url)
            result = self._get(url, operation)
            batch = result.get("results")
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise RuntimeError(f"Confluence returned an invalid {operation} result list")
            remaining = limit - len(rows)
            rows.extend(batch[:remaining])
            next_url = (result.get("_links") or {}).get("next")
            if not next_url:
                return rows, len(batch) > remaining
            if not isinstance(next_url, str):
                raise TypeError(f"Confluence returned invalid {operation} pagination")
            if next_url.startswith("/rest/"):
                next_url = f"/wiki{next_url}"
            url = urljoin(url, next_url)
        return rows, bool(url)

    def spaces(self, key=None):
        return self._list("/api/v2/spaces", "space discovery", {"keys": key} if key else None)

    def pages(self, space_id=None):
        if space_id is not None and not re.fullmatch(r"[0-9]+", str(space_id)):
            raise ValueError("Invalid Confluence space ID")
        path = "/api/v2/pages" if space_id is None else f"/api/v2/spaces/{space_id}/pages"
        return self._list(path, "page discovery", {"status": "current"})

    def search(self, keywords, *, limit=25):
        if not isinstance(keywords, str) or not keywords.strip() or len(keywords) > 200:
            raise ValueError("Supply 1 to 200 characters for Confluence keyword search")
        literal = keywords.strip().replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))
        return self._list("/rest/api/content/search", "keyword search",
                          {"cql": f'type = page AND text ~ "{literal}"'}, limit=limit)

    def publish(self, page_id, version, title, content):
        if (not self.cfg.get("enterprise.confluence_write.enabled", False)
                or page_id not in self.cfg.get("enterprise.confluence_write.page_ids", [])):
            raise ValueError("Confluence publishing is disabled or the page is outside policy scope")
        if (not re.fullmatch(r"[0-9]+", page_id) or type(version) is not int or version < 1
                or not isinstance(title, str) or not title.strip() or len(title) > 255
                or not isinstance(content, str) or not 0 < len(content.encode()) <= 100000):
            raise ValueError("Supply a page ID, the current version, a title and bounded storage content")
        current = self.read_page(page_id)
        if current["version"] != version:
            raise ValueError("Confluence page version changed; review it before publishing again")
        payload = {"id": page_id, "status": "current", "title": title,
                   "body": {"representation": "storage", "value": content}, "version": {"number": version + 1}}
        try:
            result = request_json("PUT", f"{self.base}/api/v2/pages/{page_id}", headers=self.headers, body=payload)
        except HttpError as exc:
            raise RuntimeError(f"Confluence publishing failed (HTTP {exc.status})") from None
        if str(result["id"]) != page_id or result["version"]["number"] != version + 1:
            raise ValueError("Confluence returned unexpected published page metadata")
        return {"page_id": page_id, "version": version + 1, "status": "PUBLISHED"}

    def linked_page_ids(self, links):
        return linked_page_ids(self.base, links)

    def issue_pages(self, issue, links, *, title=None, limit=5):
        page_ids = self.linked_page_ids(links)
        if not page_ids:
            matches, _ = self.search(issue, limit=limit)
            if not matches and title and len(title) <= 200:
                matches, _ = self.search(title, limit=limit)
            page_ids = [str(page["id"]) for page in matches]
        return [self.read_page(page_id) for page_id in dict.fromkeys(page_ids[:limit])]

    def read_page(self, page_id: str) -> dict:
        if not re.fullmatch(r"[0-9]+", page_id):
            raise ValueError("Confluence page ID must contain only digits")
        page = self._get(f"{self.base}/api/v2/pages/{page_id}?body-format=storage", "page read")
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


def linked_page_ids(base, links):
    origin = urlsplit(base)
    ids = {}
    for link in links:
        parsed = urlsplit(link)
        if (parsed.scheme != origin.scheme or parsed.netloc != origin.netloc
                or not parsed.path.startswith("/wiki/")):
            continue
        match = re.search(r"/pages/([0-9]+)(?:/|$)", parsed.path)
        candidates = [match[1]] if match else parse_qs(parsed.query).get("pageId", [])
        for candidate in candidates:
            if re.fullmatch(r"[0-9]+", candidate):
                ids[candidate] = None
    return list(ids)
