"""Steps 1-2: requirement ingestion (MD / TXT / HTML / JSON / DOCX) and the RAG knowledge base."""

from __future__ import annotations

import html
import json
import math
import re
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar, Protocol

from .config import Config
from .jira import split_description
from .state import Requirement

_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*\S)\s*$")
_ID_TITLE = re.compile(r"^\s*([A-Z][A-Z0-9]*-\d+)\s*[:\-\u2013]\s*(.+)$")
_TOKEN = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------- parsing

class _HtmlToText(HTMLParser):
    BLOCK: ClassVar[set[str]] = {"p", "div", "br", "tr", "ul", "ol", "table", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "li", "p", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data.replace("\n", " "))


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL)
    lines = ["".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.DOTALL)) for p in paragraphs]
    return "\n".join(html.unescape(line) for line in lines)


def parse_text(text: str, source: str = "") -> list[Requirement]:
    """A requirement starts at a `## heading` (level >= 2) or an `ID: title` line."""
    reqs: list[Requirement] = []
    title, rid, body = None, None, []

    def flush():
        if title is not None:
            desc, criteria = split_description("\n".join(body))
            reqs.append(Requirement(
                id=rid or f"REQ-{len(reqs) + 1:03d}", title=title, description=desc,
                acceptance_criteria=criteria, source=source,
            ))

    for line in text.splitlines():
        heading = _HEADING.match(line)
        candidate = heading.group(2) if heading else line
        id_title = _ID_TITLE.match(candidate)
        if id_title or (heading and len(heading.group(1)) >= 2):
            flush()
            rid, title = (id_title.group(1), id_title.group(2).strip()) if id_title else (
                None, candidate.strip())
            body = []
        elif heading:
            continue
        elif title is not None:
            body.append(line)
    flush()
    return reqs


def parse_requirements(path: Path) -> list[Requirement]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("requirements", data) if isinstance(data, dict) else data
        return [
            Requirement.from_dict({"id": f"REQ-{i:03d}", "source": str(path), **item})
            for i, item in enumerate(items, start=1)
        ]
    if suffix in (".html", ".htm"):
        parser = _HtmlToText()
        parser.feed(path.read_text(encoding="utf-8"))
        return parse_text("".join(parser.parts), str(path))
    if suffix == ".docx":
        return parse_text(_docx_text(path), str(path))
    return parse_text(path.read_text(encoding="utf-8"), str(path))


# ---------------------------------------------------------------- vector stores

class VectorStore(Protocol):
    def upsert(self, doc_id: str, text: str) -> None: ...
    def query(self, text: str, k: int) -> list[tuple[str, float]]: ...


def _vec(text: str) -> Counter:
    return Counter(_TOKEN.findall(text.lower()))


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(a[t] * b[t] for t in a.keys() & b.keys())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


class KeywordStore:
    """Dependency-free bag-of-words retrieval (default)."""

    def __init__(self) -> None:
        self.docs: dict[str, Counter] = {}

    def upsert(self, doc_id: str, text: str) -> None:
        self.docs[doc_id] = _vec(text)

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        q = _vec(text)
        scored = sorted(((d, _cosine(q, v)) for d, v in self.docs.items()), key=lambda x: -x[1])
        return [(d, s) for d, s in scored if s > 0][:k]


class ChromaStore:
    """Live ChromaDB collection (`pip install chromadb`)."""

    def __init__(self, cfg: Config):
        import chromadb

        client = chromadb.PersistentClient(path=cfg.get("knowledge_base.chroma.path"))
        kwargs = {}
        if cfg.get("knowledge_base.chroma.embedding") == "openai":
            from chromadb.utils import embedding_functions

            kwargs["embedding_function"] = embedding_functions.OpenAIEmbeddingFunction(
                api_key=cfg.secret("llm.api_key_env"), model_name="text-embedding-3-small"
            )
        self.collection = client.get_or_create_collection(
            cfg.get("knowledge_base.chroma.collection"), **kwargs
        )

    def upsert(self, doc_id: str, text: str) -> None:
        self.collection.upsert(ids=[doc_id], documents=[text])

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        res = self.collection.query(query_texts=[text], n_results=k)
        return [(i, 1.0 - float(d)) for i, d in zip(res["ids"][0], res["distances"][0])]


def make_store(cfg: Config) -> VectorStore:
    store = cfg.get("knowledge_base.store", "json")
    if store == "chroma":
        return ChromaStore(cfg)
    if store == "json":
        return KeywordStore()
    raise ValueError(f"unknown knowledge_base.store: {store}")


# ---------------------------------------------------------------- knowledge base

class KnowledgeBase:
    def __init__(self, store: VectorStore, path: Path):
        self.store = store
        self.path = path
        self.requirements: dict[str, Requirement] = {}

    def upsert(self, req: Requirement) -> None:
        self.requirements[req.id] = req
        self.store.upsert(req.id, req.text())

    def retrieve(self, query: str, k: int = 3, exclude: str | None = None) -> list[Requirement]:
        hits = self.store.query(query, k + 1)
        return [self.requirements[i] for i, _ in hits if i != exclude and i in self.requirements][:k]

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"requirements": [r.to_dict() for r in self.requirements.values()]}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return self.path
