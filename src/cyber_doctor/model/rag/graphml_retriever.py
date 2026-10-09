"""Read-only pathogen evidence retrieval from a LightRAG GraphML export."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from cyber_doctor.model.rag.document import Document


GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
DEFAULT_GRAPHML = "/home/zhangyue/experiments/mngs-lightrag-first20/final/storage/mngs_first20_pubmed_uptodate/graph_chunk_entity_relation.graphml"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _data(element: ET.Element, key_map: dict[str, str]) -> dict[str, str]:
    return {key_map.get(item.attrib.get("key", ""), item.attrib.get("key", "")): (item.text or "") for item in element.findall(f"{{{GRAPHML_NS}}}data")}


class GraphMLPathogenRetriever:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or os.getenv("MNGS_GRAPHML_PATH", DEFAULT_GRAPHML)).expanduser()
        self._nodes: dict[str, dict[str, str]] | None = None
        self._edges: list[dict[str, str]] | None = None

    def _load(self) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
        if self._nodes is not None and self._edges is not None:
            return self._nodes, self._edges
        if not self.path.is_file():
            self._nodes, self._edges = {}, []
            return self._nodes, self._edges
        root = ET.parse(self.path).getroot()
        key_map = {item.attrib.get("id", ""): item.attrib.get("attr.name", "") for item in root.findall(f"{{{GRAPHML_NS}}}key")}
        nodes = {}
        for item in root.findall(f".//{{{GRAPHML_NS}}}node"):
            values = _data(item, key_map)
            values["id"] = item.attrib.get("id", "")
            nodes[values["id"]] = values
        edges = []
        for item in root.findall(f".//{{{GRAPHML_NS}}}edge"):
            values = _data(item, key_map)
            values.update({"source": item.attrib.get("source", ""), "target": item.attrib.get("target", "")})
            edges.append(values)
        self._nodes, self._edges = nodes, edges
        return nodes, edges

    def retrieve(self, names: list[str], top_k: int = 8) -> list[Document]:
        wanted = {_norm(name) for name in names if _norm(name)}
        if not wanted:
            return []
        nodes, edges = self._load()
        hits = [node for node in nodes.values() if any(term in _norm(node.get("id")) for term in wanted)]
        hits.sort(key=lambda item: (0 if _norm(item.get("id")) in wanted else 1, -len(item.get("description", ""))))
        documents: list[Document] = []
        for node in hits[:top_k]:
            node_id = node.get("id", "")
            related = []
            for edge in edges:
                if edge.get("source") == node_id or edge.get("target") == node_id:
                    other_id = edge.get("target") if edge.get("source") == node_id else edge.get("source")
                    other = nodes.get(other_id, {})
                    related.append({"entity": other_id, "type": edge.get("keywords", ""), "description": edge.get("description", ""), "source_file": edge.get("file_path", "")})
            text = "\n".join([
                "知识图谱节点证据",
                f"实体: {node_id}",
                f"实体类型: {node.get('entity_type', '')}",
                f"节点描述: {node.get('description', '')}",
                f"图谱来源文件: {node.get('file_path', '')}",
                "关系证据:",
                *[f"- 相关实体={item['entity']}; 关系={item['type']}; 描述={item['description']}; 来源={item['source_file']}" for item in related[:12]],
            ])
            documents.append(Document(page_content=text, metadata={
                "source": "GraphML knowledge graph",
                "source_file": str(self.path),
                "title": node_id,
                "support_scope": "图谱节点和关系证据",
                "graph_entity": node_id,
                "graph_entity_type": node.get("entity_type", ""),
            }))
        return documents


INSTANCE = GraphMLPathogenRetriever()


def retrieve(names: list[str], top_k: int = 8) -> list[Document]:
    return INSTANCE.retrieve(names, top_k=top_k)
