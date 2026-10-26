"""Name-based retrieval over local structured pathogen article JSON files."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cyber_doctor.model.rag.document import Document


def configured_root() -> Path | None:
    """Return the runtime article archive configured by the deployment."""
    value = os.getenv("STRUCTURED_KNOWLEDGE_ROOT")
    return Path(value).expanduser() if value else None


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _record_names(record: dict[str, Any], folder_name: str) -> set[str]:
    names = record.get("病原名称") or {}
    pathogen = record.get("pathogen") or {}
    if not isinstance(names, dict):
        names = {}
    if not isinstance(pathogen, dict):
        pathogen = {}
    species = pathogen.get("种") or pathogen.get("species") or {}
    genus = pathogen.get("属") or pathogen.get("genus") or {}
    if not isinstance(species, dict):
        species = {}
    if not isinstance(genus, dict):
        genus = {}
    aliases = record.get("aliases") or pathogen.get("别名") or pathogen.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    values = [
        record.get("NameID"), record.get("name_id"), pathogen.get("NameID"), folder_name,
        names.get("种-拉丁名"), names.get("种-中文名"),
        names.get("属-拉丁名"), names.get("属-中文名"),
        pathogen.get("species_latin"), pathogen.get("species_chinese"),
        pathogen.get("genus_latin"), pathogen.get("genus_chinese"),
        species.get("拉丁名"), species.get("中文名"), species.get("latin"), species.get("chinese"),
        genus.get("拉丁名"), genus.get("中文名"), genus.get("latin"), genus.get("chinese"),
        *(aliases if isinstance(aliases, list) else []),
    ]
    return {_normalize(value) for value in values if _normalize(value)}


def _species_names(record: dict[str, Any], folder_name: str) -> set[str]:
    names = record.get("病原名称") or {}
    pathogen = record.get("pathogen") or {}
    if not isinstance(names, dict):
        names = {}
    if not isinstance(pathogen, dict):
        pathogen = {}
    species = pathogen.get("种") or pathogen.get("species") or {}
    if not isinstance(species, dict):
        species = {}
    values = [
        record.get("NameID"), record.get("name_id"), pathogen.get("NameID"), folder_name,
        names.get("种-拉丁名"), names.get("种-中文名"),
        pathogen.get("species_latin"), pathogen.get("species_chinese"),
        species.get("拉丁名"), species.get("中文名"), species.get("latin"), species.get("chinese"),
    ]
    return {_normalize(value) for value in values if _normalize(value)}


def _article_source(record: dict[str, Any]) -> dict[str, Any]:
    source = record.get("source")
    return source if isinstance(source, dict) else {}


def _article_title(record: dict[str, Any], path: Path) -> str:
    article = record.get("文章信息") or {}
    source = _article_source(record)
    return str(article.get("标题") or source.get("title") or path.stem)


def _structured_text(record: dict[str, Any], path: Path) -> str:
    source = _article_source(record)
    metadata = {
        "来源": record.get("来源", source.get("type", "")),
        "NameID": record.get("NameID") or record.get("name_id") or (record.get("pathogen") or {}).get("NameID", ""),
        "文章信息": record.get("文章信息", {}),
        "病原名称": record.get("病原名称") or record.get("pathogen", {}),
        "文献来源": source,
        "来源文件": path.name,
        "证据范围": record.get("scope") or record.get("evidence_scope") or "unknown",
    }
    legacy_source = record.get("来源")
    body = {
        "PubMed": {
            "研究或病例信息": record.get("研究或病例信息", {}),
            "病原致病信息": record.get("病原致病信息", {}),
            "治疗和结局": record.get("治疗和结局", {}),
        },
        "UpToDate": {
            "致病性": record.get("致病性", {}),
            "治疗方案和预后": record.get("治疗方案和预后", {}),
        },
    }.get(legacy_source, {})
    if "evidence" in record:
        return "\n".join([
            "结构化文章证据",
            json.dumps(metadata, ensure_ascii=False),
            "逐条原文与范围: " + json.dumps(record.get("evidence") or [], ensure_ascii=False),
        ])
    return "\n".join([
        "结构化文章证据",
        json.dumps(metadata, ensure_ascii=False),
        json.dumps(body, ensure_ascii=False),
        "证据条目：" + json.dumps(record.get("证据") or record.get("evidence") or [], ensure_ascii=False),
    ])


class StructuredPathogenRetriever:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        configured = Path(root).expanduser() if root else configured_root()
        self.root = configured.resolve() if configured else None
        self._records: list[tuple[set[str], Path, dict[str, Any]]] | None = None

    def _load(self) -> list[tuple[set[str], Path, dict[str, Any]]]:
        if self._records is not None:
            return self._records
        records: list[tuple[set[str], Path, dict[str, Any]]] = []
        if self.root and self.root.is_file():
            paths = [self.root]
        elif self.root and self.root.is_dir():
            paths = sorted(self.root.rglob("*.json"))
        else:
            paths = []
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and (record.get("病原名称") or record.get("pathogen")):
                records.append((_record_names(record, path.parent.name), path, record))
        self._records = records
        return records

    def retrieve(self, names: list[str], top_k: int = 12) -> list[Document]:
        wanted = {_normalize(name) for name in names if _normalize(name)}
        if not wanted:
            return []
        matched: list[Document] = []
        for keys, path, record in self._load():
            if not wanted.intersection(keys):
                continue
            matched.append(Document(
                page_content=_structured_text(record, path),
                metadata={
                    "source": record.get("来源") or _article_source(record).get("type", ""),
                    "source_file": str(path),
                    "title": _article_title(record, path),
                    "name_id": record.get("NameID") or record.get("name_id") or (record.get("pathogen") or {}).get("NameID", ""),
                    "evidence_scope": record.get("scope") or record.get("evidence_scope") or "unknown",
                    "structured_record": record,
                },
            ))
        return matched[:top_k]

    def find_pathogen(self, query: str) -> dict[str, Any] | None:
        """Return the best matching structured record for a free-text query."""
        normalized_query = _normalize(query)
        if not normalized_query:
            return None
        candidates: list[tuple[int, dict[str, Any]]] = []
        for _keys, _path, record in self._load():
            names = sorted(_species_names(record, _path.parent.name), key=len, reverse=True)
            for name in names:
                if len(name) >= 4 and name in normalized_query:
                    candidates.append((len(name), record))
                    break
        return max(candidates, key=lambda item: item[0])[1] if candidates else None


INSTANCE = StructuredPathogenRetriever()


def _instance() -> StructuredPathogenRetriever:
    global INSTANCE
    root = configured_root()
    resolved = root.resolve() if root else None
    if INSTANCE.root != resolved:
        INSTANCE = StructuredPathogenRetriever(root)
    return INSTANCE


def retrieve(names: list[str], top_k: int = 12) -> list[Document]:
    return _instance().retrieve(names, top_k=top_k)


def find_pathogen(query: str) -> dict[str, Any] | None:
    return _instance().find_pathogen(query)
