"""Name-based retrieval over local structured pathogen article JSON files."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cyber_doctor.model.rag.document import Document


DEFAULT_ROOT = Path(os.getenv(
    "STRUCTURED_KNOWLEDGE_ROOT",
    r"E:\华大医疗agent资料\病原结构化信息_前20_按文章",
))


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _record_names(record: dict[str, Any], folder_name: str) -> set[str]:
    names = record.get("病原名称") or {}
    values = [
        record.get("NameID"), folder_name,
        names.get("种-拉丁名"), names.get("种-中文名"),
        names.get("属-拉丁名"), names.get("属-中文名"),
    ]
    return {_normalize(value) for value in values if _normalize(value)}


def _species_names(record: dict[str, Any], folder_name: str) -> set[str]:
    names = record.get("病原名称") or {}
    values = [
        record.get("NameID"), folder_name,
        names.get("种-拉丁名"), names.get("种-中文名"),
    ]
    return {_normalize(value) for value in values if _normalize(value)}


def _structured_text(record: dict[str, Any], path: Path) -> str:
    metadata = {
        "来源": record.get("来源", ""),
        "NameID": record.get("NameID", ""),
        "文章信息": record.get("文章信息", {}),
        "病原名称": record.get("病原名称", {}),
        "来源文件": path.name,
    }
    source = record.get("来源")
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
    }.get(source, {})
    return "\n".join([
        "结构化文章证据",
        json.dumps(metadata, ensure_ascii=False),
        json.dumps(body, ensure_ascii=False),
        "证据条目：" + json.dumps(record.get("证据") or [], ensure_ascii=False),
    ])


class StructuredPathogenRetriever:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root or DEFAULT_ROOT).expanduser()
        self._records: list[tuple[set[str], Path, dict[str, Any]]] | None = None

    def _load(self) -> list[tuple[set[str], Path, dict[str, Any]]]:
        if self._records is not None:
            return self._records
        records: list[tuple[set[str], Path, dict[str, Any]]] = []
        if self.root.is_dir():
            for folder in sorted(self.root.iterdir()):
                if not folder.is_dir():
                    continue
                # Support both historical flat pathogen folders and the newer
                # pathogen/PubMed + pathogen/UpToDate layout.
                for path in sorted(folder.rglob("*.json")):
                    try:
                        record = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(record, dict):
                        records.append((_record_names(record, folder.name), path, record))
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
                    "source": record.get("来源", ""),
                    "source_file": str(path),
                    "title": (record.get("文章信息") or {}).get("标题", path.stem),
                    "name_id": record.get("NameID", ""),
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


def retrieve(names: list[str], top_k: int = 12) -> list[Document]:
    return INSTANCE.retrieve(names, top_k=top_k)


def find_pathogen(query: str) -> dict[str, Any] | None:
    return INSTANCE.find_pathogen(query)
