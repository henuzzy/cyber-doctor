"""Runtime retrieval for the five-dimension pathogen knowledge archive.

The archive is deployment data and is intentionally loaded from an explicit
file or directory.  It is never bundled into the Python package and no local
machine path is used as a default.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cyber_doctor.model.rag.document import Document


DIMENSIONS = ("物种描述", "致病性", "毒力因子", "耐药性", "治疗与预后")
_ROOT_ENV_KEYS = ("FIVE_DIMENSION_KNOWLEDGE_ROOT", "MNGS_FIVE_DIMENSION_ROOT", "PATHOGEN_KNOWLEDGE_ROOT")


def configured_root() -> Path | None:
    for key in _ROOT_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return Path(value).expanduser()
    return None


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("_", " ")
    text = re.sub(r"[\s\-‐‑‒–—·.,;:|/\\()\[\]{}<>]+", "", text)
    return text


def _text(value: Any) -> str:
    return str(value or "").strip()


def _names(record: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    info = record.get("病原信息") or record.get("pathogen_info") or {}
    if not isinstance(info, dict):
        info = {}
    taxonomy = info.get("名称与分类") or info.get("taxonomy") or {}
    if not isinstance(taxonomy, dict):
        taxonomy = {}
    species = taxonomy.get("种") or taxonomy.get("species") or {}
    genus = taxonomy.get("属") or taxonomy.get("genus") or {}
    species = species if isinstance(species, dict) else {}
    genus = genus if isinstance(genus, dict) else {}
    aliases = info.get("别名") or info.get("aliases") or record.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    aliases = [_text(item) for item in aliases if _text(item)] if isinstance(aliases, list) else []
    species_values = {
        "中文种名": _text(species.get("中文名") or species.get("chinese")),
        "拉丁种名": _text(species.get("拉丁名") or species.get("latin")),
        "NameID": _text(info.get("NameID") or record.get("NameID")),
    }
    genus_values = {
        "中文属名": _text(genus.get("中文名") or genus.get("chinese")),
        "拉丁属名": _text(genus.get("拉丁名") or genus.get("latin")),
    }
    flat_species = {
        "中文种名": _text(info.get("种-中文名") or info.get("species_chinese")),
        "拉丁种名": _text(info.get("种-拉丁名") or info.get("species_latin")),
    }
    flat_genus = {
        "中文属名": _text(info.get("属-中文名") or info.get("genus_chinese")),
        "拉丁属名": _text(info.get("属-拉丁名") or info.get("genus_latin")),
    }
    species_values.update({key: value for key, value in flat_species.items() if value})
    genus_values.update({key: value for key, value in flat_genus.items() if value})
    return species_values, genus_values, aliases


def _record_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("病原列表"), list):
        return [item for item in value["病原列表"] if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("pathogens"), list):
        return [item for item in value["pathogens"] if isinstance(item, dict)]
    if isinstance(value, dict) and (value.get("病原信息") or value.get("pathogen_info")):
        return [value]
    return []


def _candidate_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.json"))


def validate_profile(record: dict[str, Any]) -> None:
    """Validate the runtime schema; older archives may use a single source URL."""
    info = record.get("病原信息")
    knowledge = record.get("五维知识")
    if not isinstance(info, dict) or not isinstance(info.get("名称与分类"), dict):
        raise ValueError("profile requires 病原信息.名称与分类")
    if not isinstance(knowledge, dict):
        raise ValueError("profile requires 五维知识")
    for dimension in DIMENSIONS:
        item = knowledge.get(dimension)
        if not isinstance(item, dict) or "概述" not in item or "来源" not in item:
            raise ValueError(f"invalid knowledge dimension: {dimension}")
        if item["概述"] is not None and not isinstance(item["概述"], str):
            raise ValueError(f"invalid summary: {dimension}")
        if item["来源"] is not None and not isinstance(item["来源"], (str, list)):
            raise ValueError(f"invalid sources: {dimension}")


def _name_in_query(value: str, query: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", value):
        return normalize_name(value) in normalize_name(query)
    value = re.sub(r"[_\s]+", " ", value).strip()
    query = re.sub(r"[_\s]+", " ", query).strip()
    return bool(re.search(r"(?<![\w])" + re.escape(value) + r"(?![\w])", query, re.I))


@dataclass(frozen=True)
class FiveDimensionMatch:
    record: dict[str, Any]
    source_file: str
    matched_by: str
    support_scope: str
    score: int


class FiveDimensionPathogenRetriever:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        configured = Path(root).expanduser() if root else configured_root()
        self.root = configured.resolve() if configured else None
        self._records: list[tuple[dict[str, Any], Path, dict[str, str], dict[str, str], list[str]]] | None = None

    def _load(self) -> list[tuple[dict[str, Any], Path, dict[str, str], dict[str, str], list[str]]]:
        if self._records is not None:
            return self._records
        records: list[tuple[dict[str, Any], Path, dict[str, str], dict[str, str], list[str]]] = []
        if self.root:
            for path in _candidate_files(self.root):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    logging.getLogger(__name__).warning("Cannot load knowledge archive %s: %s", path, exc)
                    continue
                for record in _record_list(value):
                    try:
                        validate_profile(record)
                    except ValueError as exc:
                        logging.getLogger(__name__).warning("Invalid profile in %s: %s", path, exc)
                        continue
                    species, genus, aliases = _names(record)
                    if any(species.values()) or any(genus.values()):
                        records.append((record, path, species, genus, aliases))
        self._records = records
        return records

    def reload(self) -> None:
        self._records = None

    def search(self, query: str, top_k: int = 8) -> list[FiveDimensionMatch]:
        query_norm = normalize_name(query)
        if not query_norm or top_k < 1:
            return []
        matches: list[FiveDimensionMatch] = []
        for record, path, species, genus, aliases in self._load():
            best: tuple[int, str, str] | None = None
            for label, value in (*species.items(), *[("别名", alias) for alias in aliases]):
                key = normalize_name(value)
                if not key:
                    continue
                if key == query_norm or _name_in_query(value, query):
                    score = 100 if key == query_norm else 85
                    candidate = (score, label, "目标物种档案")
                    if best is None or candidate[0] > best[0]:
                        best = candidate
            for label, value in genus.items():
                key = normalize_name(value)
                if not key:
                    continue
                if key == query_norm or _name_in_query(value, query):
                    score = 65 if key == query_norm else 50
                    candidate = (score, label, "同属病原群档案")
                    if best is None or candidate[0] > best[0]:
                        best = candidate
            if best:
                matches.append(FiveDimensionMatch(record, str(path), best[1], best[2], best[0]))
        matches.sort(key=lambda item: item.score, reverse=True)
        if matches and matches[0].score >= 85:
            matches = [item for item in matches if item.score >= 85]
        unique: list[FiveDimensionMatch] = []
        seen: set[tuple[str, str]] = set()
        for item in matches:
            info = item.record.get("病原信息") or item.record.get("pathogen_info") or {}
            species, _genus, _aliases = _names(item.record)
            identity = _text(info.get("NameID") or item.record.get("NameID") or species.get("拉丁种名") or species.get("中文种名"))
            key = (identity or item.source_file, item.support_scope)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= top_k:
                break
        return unique

    def retrieve(self, names: Iterable[str], top_k: int = 8) -> list[Document]:
        if top_k < 1:
            return []
        matches: list[FiveDimensionMatch] = []
        seen: set[tuple[str, str]] = set()
        for name in names:
            for item in self.search(str(name), top_k=top_k):
                info = item.record.get("病原信息") or {}
                species, _genus, _aliases = _names(item.record)
                identity = _text(info.get("NameID") or item.record.get("NameID") or species.get("拉丁种名") or species.get("中文种名"))
                key = (identity or item.source_file, item.support_scope)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(item)
                if len(matches) >= top_k:
                    break
            if len(matches) >= top_k:
                break
        return [self._document(item) for item in matches]

    def find_pathogen(self, query: str) -> dict[str, Any] | None:
        matches = self.search(query, top_k=1)
        return matches[0].record if matches else None

    def is_exact_name(self, query: str) -> bool:
        """Whether the complete input is a stored species/genus name or alias."""
        query_norm = normalize_name(query)
        if not query_norm:
            return False
        for _record, _path, species, genus, aliases in self._load():
            values = [*species.values(), *genus.values(), *aliases]
            if any(normalize_name(value) == query_norm for value in values):
                return True
        return False

    @staticmethod
    def _document(match: FiveDimensionMatch) -> Document:
        record = match.record
        info = record.get("病原信息") or {}
        taxonomy = info.get("名称与分类") or {}
        knowledge = record.get("五维知识") or record.get("knowledge") or {}
        lines = [
            "五维病原知识档案",
            f"证据范围: {match.support_scope}",
            f"匹配字段: {match.matched_by}",
            f"NameID: {_text(info.get('NameID') or record.get('NameID'))}",
            f"类别: {_text(info.get('类别'))}",
            "病原名称与分类: " + json.dumps(taxonomy, ensure_ascii=False),
            "名称匹配范围不等于原文证据范围；未标注范围的摘要不能认定为目标物种直接证据。",
        ]
        for dimension in DIMENSIONS:
            item = knowledge.get(dimension) or {}
            if not isinstance(item, dict):
                item = {"概述": item}
            summary = _text(item.get("概述") or item.get("summary"))
            sources = item.get("来源") or item.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            lines.append(f"{dimension}: {summary or '未提供'}")
            lines.append(f"{dimension}来源: {json.dumps(sources, ensure_ascii=False)}")
            lines.append(f"{dimension}证据范围: {_text(item.get('证据范围') or item.get('scope')) or '未单独标注，请结合概述和逐篇原文核查'}")
        return Document(
            page_content="\n".join(lines),
            metadata={
                "knowledge_kind": "five_dimension",
                "support_scope": match.support_scope,
                "matched_by": match.matched_by,
                "source_file": match.source_file,
                "source": "五维病原知识档案",
                "title": _text((taxonomy.get("种") or {}).get("中文名") or (taxonomy.get("种") or {}).get("拉丁名") or "病原五维档案"),
                "name_id": _text(info.get("NameID") or record.get("NameID")),
                "five_dimension_record": record,
            },
        )


INSTANCE = FiveDimensionPathogenRetriever()


def _instance() -> FiveDimensionPathogenRetriever:
    global INSTANCE
    root = configured_root()
    resolved = root.resolve() if root else None
    if INSTANCE.root != resolved:
        INSTANCE = FiveDimensionPathogenRetriever(root)
    return INSTANCE


def retrieve(names: Iterable[str], top_k: int = 8) -> list[Document]:
    return _instance().retrieve(names, top_k=top_k)


def search(query: str, top_k: int = 8) -> list[FiveDimensionMatch]:
    return _instance().search(query, top_k=top_k)


def find_pathogen(query: str) -> dict[str, Any] | None:
    return _instance().find_pathogen(query)


def is_exact_name(query: str) -> bool:
    return _instance().is_exact_name(query)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Query the five-dimension pathogen archive")
    parser.add_argument("query")
    parser.add_argument("--knowledge-root", default=str(configured_root() or ""))
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if not args.knowledge_root or not Path(args.knowledge_root).exists():
        parser.error("provide an existing --knowledge-root or FIVE_DIMENSION_KNOWLEDGE_ROOT")
    matches = FiveDimensionPathogenRetriever(args.knowledge_root).search(args.query, args.top_k)
    print(json.dumps([{"档案": item.record, "匹配范围": item.support_scope,
                       "匹配字段": item.matched_by} for item in matches], ensure_ascii=False, indent=2))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
