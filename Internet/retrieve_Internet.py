"""Read downloaded web pages and turn them into context for internet search."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from env import get_app_root


_SAVE_PATH = Path(get_app_root()) / "data" / "cache" / "internet"
_MAX_DOCS = 6
_MAX_CHARS_PER_DOC = 2500


@dataclass
class WebDocument:
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def format_docs(docs: list[WebDocument]) -> str:
    return "\n-------------分割线--------------\n".join(doc.page_content for doc in docs)


def retrieve_html(question: str) -> tuple[list[WebDocument], str]:
    docs = load_downloaded_html(_SAVE_PATH)
    context = format_docs(docs)
    print(context)
    return docs, context


def load_downloaded_html(root: Path) -> list[WebDocument]:
    if not root.exists():
        return []

    docs: list[WebDocument] = []
    for path in sorted(root.glob("*.html"))[:_MAX_DOCS]:
        text = extract_html_text(path)
        if not text:
            continue
        docs.append(
            WebDocument(
                page_content=text[:_MAX_CHARS_PER_DOC],
                metadata={"source_file": os.fspath(path), "title": path.stem},
            )
        )
    return docs


def extract_html_text(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()
