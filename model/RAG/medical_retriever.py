from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from model.RAG.document import Document


load_dotenv(".env", override=False)

SparseVector = dict[int, float]

DEFAULT_COLLECTION = os.getenv("MEDICAL_TEXTBOOK_COLLECTION", "medical_textbooks")
DEFAULT_URI = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
DEFAULT_TOKEN = os.getenv("MILVUS_TOKEN") or None
DEFAULT_TIMEOUT = float(os.getenv("MILVUS_TIMEOUT", "10"))
DEFAULT_MODEL = os.getenv("BGE_M3_MODEL", r"D:\models\huggingface\hub\models--BAAI--bge-m3")
DEFAULT_DEVICE = os.getenv("BGE_M3_DEVICE", "auto")
DEFAULT_USE_FP16 = os.getenv("BGE_M3_USE_FP16", "1") == "1"
DEFAULT_MAX_LENGTH = int(os.getenv("BGE_M3_MAX_LENGTH", "1024"))
DEFAULT_TOP_K = int(os.getenv("MEDICAL_RETRIEVE_TOP_K", "12"))
DEFAULT_CANDIDATE_K = int(os.getenv("MEDICAL_RETRIEVE_CANDIDATE_K", "40"))
DEFAULT_DENSE_WEIGHT = float(os.getenv("MEDICAL_DENSE_WEIGHT", "0.7"))
DEFAULT_SPARSE_WEIGHT = float(os.getenv("MEDICAL_SPARSE_WEIGHT", "0.3"))

OUTPUT_FIELDS = [
    "id",
    "text",
    "answer_text",
    "source_id",
    "source_file",
    "doc_type",
    "section_path",
    "chunk_index",
    "metadata",
]


class MedicalHybridRetriever:
    def __init__(
        self,
        uri: str = DEFAULT_URI,
        token: str | None = DEFAULT_TOKEN,
        timeout: float = DEFAULT_TIMEOUT,
        collection_name: str = DEFAULT_COLLECTION,
        model_name: str = DEFAULT_MODEL,
        device: str = DEFAULT_DEVICE,
        use_fp16: bool = DEFAULT_USE_FP16,
        max_length: int = DEFAULT_MAX_LENGTH,
    ) -> None:
        self.uri = uri
        self.token = token
        self.timeout = timeout
        self.collection_name = collection_name
        self.model_name = resolve_model_path(model_name)
        self.device = device
        self.use_fp16 = use_fp16
        self.max_length = max_length
        self._client = None
        self._model = None

    @property
    def client(self):
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=self.uri, token=self.token, timeout=self.timeout)
        return self._client

    @property
    def model(self):
        if self._model is None:
            self._model = load_embedding_model(self.model_name, self.use_fp16, self.device)
        return self._model

    def retrieve(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        dense_weight: float = DEFAULT_DENSE_WEIGHT,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
    ) -> list[Document]:
        if not query or not query.strip():
            return []
        if not self.client.has_collection(self.collection_name, timeout=self.timeout):
            raise RuntimeError(f"Milvus collection {self.collection_name!r} does not exist.")

        # Runtime retrieval mirrors offline ingestion: query once, search dense and sparse fields,
        # then fuse both routes into one ranked evidence list for the agent.
        dense_vectors, sparse_vectors = encode_batch(self.model, [query], batch_size=1, max_length=self.max_length)
        self.client.load_collection(collection_name=self.collection_name, timeout=self.timeout)
        candidate_k = max(candidate_k, top_k)
        dense_hits = search_vector(self.client, self.collection_name, dense_vectors[0], "vector", "COSINE", candidate_k, self.timeout)
        sparse_hits = search_vector(self.client, self.collection_name, sparse_vectors[0], "sparse_vector", "IP", candidate_k, self.timeout)
        fused_hits = weighted_fuse(dense_hits, sparse_hits, top_k=top_k, dense_weight=dense_weight, sparse_weight=sparse_weight)
        return [hit_to_document(hit) for hit in fused_hits]


def retrieve(query: str, top_k: int = DEFAULT_TOP_K) -> list[Document]:
    return INSTANCE.retrieve(query, top_k=top_k)


def resolve_model_path(model_name: str) -> str:
    # Accept both a real model directory and a HuggingFace cache root like models--BAAI--bge-m3.
    path = Path(model_name)
    if not path.exists():
        return model_name
    if (path / "config.json").exists():
        return str(path)

    refs_main = path / "refs" / "main"
    snapshots_dir = path / "snapshots"
    if refs_main.exists() and snapshots_dir.exists():
        snapshot_name = refs_main.read_text(encoding="utf-8").strip()
        snapshot_path = snapshots_dir / snapshot_name
        if (snapshot_path / "config.json").exists():
            return str(snapshot_path)

    if snapshots_dir.exists():
        candidates = sorted(
            [item for item in snapshots_dir.iterdir() if (item / "config.json").exists()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])

    return str(path)


def load_embedding_model(model_name: str, use_fp16: bool, device_mode: str):
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError("FlagEmbedding is not installed. Run: python -m pip install FlagEmbedding") from exc
    device = resolve_device(device_mode)
    fp16_enabled = bool(use_fp16 and device.startswith("cuda"))
    patch_transformers_dtype_argument()
    print(
        "[medical_retriever] loading BGE-M3 "
        f"python={sys.executable} model={model_name} device={device} fp16={fp16_enabled}",
        flush=True,
    )
    try:
        return BGEM3FlagModel(model_name, use_fp16=fp16_enabled, devices=device)
    except TypeError as exc:
        if "unexpected keyword argument 'dtype'" not in str(exc):
            raise
        raise RuntimeError(
            "BGE-M3 failed to load because the active Python environment has an incompatible "
            "FlagEmbedding/transformers combination. Start the app with the project venv "
            r"(.\.venv\Scripts\python.exe app.py) or install requirements.txt in the active environment. "
            f"Active python: {sys.executable}"
        ) from exc


def patch_transformers_dtype_argument() -> None:
    """Allow FlagEmbedding 1.4.0 to run on transformers 4.x.

    FlagEmbedding passes dtype=... into AutoModel.from_pretrained. Transformers 4.x
    expects torch_dtype=..., so without this shim BGE-M3 fails before retrieval starts.
    """
    try:
        from transformers import AutoModel
    except Exception:
        return
    if getattr(AutoModel.from_pretrained, "_cyber_doctor_dtype_patch", False):
        return

    original = AutoModel.from_pretrained

    def patched_from_pretrained(*args, **kwargs):
        if "dtype" in kwargs and "torch_dtype" not in kwargs:
            kwargs["torch_dtype"] = kwargs.pop("dtype")
        return original(*args, **kwargs)

    patched_from_pretrained._cyber_doctor_dtype_patch = True
    AutoModel.from_pretrained = patched_from_pretrained


def resolve_device(device_mode: str) -> str:
    device_mode = (device_mode or "auto").strip().lower()
    try:
        import torch
    except Exception as exc:
        raise RuntimeError("torch is not installed. Install a CUDA-enabled torch build for GPU retrieval.") from exc

    cuda_available = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
    if device_mode == "auto":
        return "cuda:0" if cuda_available else "cpu"
    if device_mode == "cuda":
        if not cuda_available:
            raise RuntimeError("CUDA is not available in this Python environment.")
        return "cuda:0"
    return "cpu"


def encode_batch(model: object, texts: list[str], batch_size: int, max_length: int) -> tuple[list[list[float]], list[SparseVector]]:
    encoded = model.encode(
        texts,
        batch_size=batch_size,
        max_length=max_length,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    return to_dense_vectors(encoded["dense_vecs"]), to_sparse_vectors(encoded["lexical_weights"])


def to_dense_vectors(raw_vectors: object) -> list[list[float]]:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    return [list(map(float, vector)) for vector in raw_vectors]


def to_sparse_vectors(raw_weights: object) -> list[SparseVector]:
    sparse_vectors: list[SparseVector] = []
    for weights in raw_weights:
        sparse_vector: SparseVector = {}
        for token_id, weight in weights.items():
            value = float(weight)
            if value > 0:
                sparse_vector[int(token_id)] = value
        sparse_vectors.append(sparse_vector)
    return sparse_vectors


def search_vector(
    client: object,
    collection_name: str,
    vector: list[float] | SparseVector,
    anns_field: str,
    metric_type: str,
    top_k: int,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    results = client.search(
        collection_name=collection_name,
        data=[vector],
        anns_field=anns_field,
        limit=top_k,
        output_fields=OUTPUT_FIELDS,
        search_params={"metric_type": metric_type},
        timeout=timeout,
    )
    hits = results[0] if results else []
    payload: list[dict] = []
    for rank, hit in enumerate(hits, start=1):
        entity = hit.get("entity") or {}
        if hasattr(entity, "to_dict"):
            entity = entity.to_dict()
        payload.append(
            {
                "rank": rank,
                "id": entity.get("id") or hit.get("id"),
                "score": hit.get("distance"),
                "text": entity.get("text") or "",
                "answer_text": entity.get("answer_text") or "",
                "source_id": entity.get("source_id") or "",
                "source_file": entity.get("source_file") or "",
                "doc_type": entity.get("doc_type") or "",
                "section_path": entity.get("section_path") or "",
                "chunk_index": entity.get("chunk_index"),
                "metadata": entity.get("metadata") or {},
            }
        )
    return payload


def weighted_fuse(
    dense_hits: list[dict],
    sparse_hits: list[dict],
    top_k: int,
    dense_weight: float,
    sparse_weight: float,
) -> list[dict]:
    # Normalize each route independently so dense cosine and sparse IP scores can be combined.
    total = dense_weight + sparse_weight
    dense_weight = dense_weight / total if total > 0 else 0.5
    sparse_weight = sparse_weight / total if total > 0 else 0.5
    scores: dict[str, float] = defaultdict(float)
    payloads: dict[str, dict] = {}
    for hit, score in normalized_hit_scores(dense_hits):
        hit_id = str(hit.get("id") or "")
        if hit_id:
            scores[hit_id] += dense_weight * score
            payloads.setdefault(hit_id, hit)
    for hit, score in normalized_hit_scores(sparse_hits):
        hit_id = str(hit.get("id") or "")
        if hit_id:
            scores[hit_id] += sparse_weight * score
            payloads.setdefault(hit_id, hit)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
    return [{**payloads[hit_id], "weighted_score": score} for hit_id, score in ranked]


def normalized_hit_scores(hits: list[dict]) -> list[tuple[dict, float]]:
    if not hits:
        return []
    raw_scores = [safe_float(hit.get("score"), 0.0) for hit in hits]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    if max_score == min_score:
        return [(hit, 1.0) for hit in hits]
    return [(hit, (score - min_score) / (max_score - min_score)) for hit, score in zip(hits, raw_scores)]


def hit_to_document(hit: dict[str, Any]) -> Document:
    metadata = dict(hit.get("metadata") or {})
    metadata.update(
        {
            "id": hit.get("id"),
            "source_file": hit.get("source_file"),
            "source_id": hit.get("source_id"),
            "doc_type": hit.get("doc_type"),
            "section": hit.get("section_path"),
            "section_path": hit.get("section_path"),
            "chunk_index": hit.get("chunk_index"),
            "score": hit.get("score"),
            "weighted_score": hit.get("weighted_score"),
            "retriever": "medical_hybrid_bge_m3",
        }
    )
    return Document(page_content=str(hit.get("answer_text") or ""), metadata=metadata)


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


INSTANCE = MedicalHybridRetriever()
