from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_INPUT_DIR = Path(os.getenv("MEDICAL_CHUNKS_DIR", r"E:\华大医疗agent清洗版chunks"))
DEFAULT_COLLECTION = os.getenv("MEDICAL_TEXTBOOK_COLLECTION", "medical_textbooks")
DEFAULT_URI = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
DEFAULT_TOKEN = os.getenv("MILVUS_TOKEN") or None
DEFAULT_MODEL = os.getenv("BGE_M3_MODEL", r"D:\models\huggingface\hub\models--BAAI--bge-m3")
DEFAULT_BATCH_SIZE = int(os.getenv("BGE_M3_BATCH_SIZE", "16"))
DEFAULT_MAX_LENGTH = int(os.getenv("BGE_M3_MAX_LENGTH", "1024"))
DEFAULT_USE_FP16 = os.getenv("BGE_M3_USE_FP16", "0") == "1"
DEFAULT_DEVICE = os.getenv("BGE_M3_DEVICE", "auto")
DEFAULT_RETRIEVE_TOP_K = int(os.getenv("MEDICAL_RETRIEVE_TOP_K", "12"))
DEFAULT_RETRIEVE_CANDIDATE_K = int(os.getenv("MEDICAL_RETRIEVE_CANDIDATE_K", "40"))
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

SparseVector = dict[int, float]


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed medical chunk JSONL files into Milvus.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Directory containing *.chunks.jsonl files.")
    parser.add_argument("--uri", default=DEFAULT_URI, help="Milvus URI, for example http://127.0.0.1:19530")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Optional Milvus token.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Target Milvus collection name.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Local BGE-M3 model path or HuggingFace model name.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Embedding and upsert batch size.")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH, help="Max token length for BGE-M3.")
    parser.add_argument("--use-fp16", action="store_true", default=DEFAULT_USE_FP16, help="Use fp16 on CUDA.")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default=DEFAULT_DEVICE)
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the target collection.")
    parser.add_argument("--probe-query", default=None, help="Optional query to search after ingestion.")
    parser.add_argument("--probe-top-k", type=int, default=DEFAULT_RETRIEVE_TOP_K)
    parser.add_argument("--candidate-k", type=int, default=DEFAULT_RETRIEVE_CANDIDATE_K, help="Candidates per dense/sparse route before fusion.")
    parser.add_argument("--dense-weight", type=float, default=DEFAULT_DENSE_WEIGHT)
    parser.add_argument("--sparse-weight", type=float, default=DEFAULT_SPARSE_WEIGHT)
    parser.add_argument("--log-every", type=int, default=1, help="Print progress every N batches.")
    args = parser.parse_args()

    started_at = time.perf_counter()
    log(f"input_dir={args.input_dir}")
    log(f"collection={args.collection}, uri={args.uri}")

    log("importing pymilvus...")
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise SystemExit("pymilvus is not installed. Run: python -m pip install pymilvus") from exc

    log("loading chunk jsonl files...")
    chunks = load_chunks(Path(args.input_dir))
    if not chunks:
        raise SystemExit(f"No chunks found under {args.input_dir}")
    log(f"loaded {len(chunks)} chunks")

    model_path = resolve_model_path(args.model)
    log(f"resolved embedding model: {model_path}")
    log("connecting to Milvus...")
    client = MilvusClient(uri=args.uri, token=args.token)
    log("Milvus client ready")
    log(f"loading BGE-M3 model on device={args.device}, fp16={args.use_fp16}...")
    model = load_embedding_model(model_path, args.use_fp16, args.device)
    log("BGE-M3 model ready")

    total = len(chunks)
    processed = 0
    collection_ready = False
    total_batches = (total + args.batch_size - 1) // args.batch_size
    for batch_no, batch in enumerate(chunked(chunks, args.batch_size), start=1):
        batch_started_at = time.perf_counter()
        if should_log_batch(batch_no, args.log_every, total_batches):
            log(f"batch {batch_no}/{total_batches}: embedding {len(batch)} chunks...")
        texts = [chunk["search_text"] for chunk in batch]
        dense_vectors, sparse_vectors = encode_batch(model, texts, min(args.batch_size, len(texts)), args.max_length)
        if not collection_ready:
            # Create the collection after the first embedding batch so the dense dimension is exact.
            log("checking/creating Milvus collection...")
            ensure_collection(client, args.collection, len(dense_vectors[0]), recreate=args.recreate)
            collection_ready = True
        rows = [chunk_to_row(chunk, dense, sparse) for chunk, dense, sparse in zip(batch, dense_vectors, sparse_vectors)]
        if should_log_batch(batch_no, args.log_every, total_batches):
            log(f"batch {batch_no}/{total_batches}: upserting {len(rows)} rows...")
        client.upsert(collection_name=args.collection, data=rows)
        processed += len(rows)
        if should_log_batch(batch_no, args.log_every, total_batches):
            pct = processed * 100 / total
            elapsed = time.perf_counter() - batch_started_at
            log(f"upserted {processed}/{total} ({pct:.1f}%), batch_sec={elapsed:.1f}")

    log("flushing Milvus collection...")
    client.flush(collection_name=args.collection)
    log(f"finished in {time.perf_counter() - started_at:.1f}s")
    print(
        json.dumps(
            {
                "collection": args.collection,
                "uri": args.uri,
                "chunks": total,
                "model": model_path,
                "dense_field": "vector",
                "sparse_field": "sparse_vector",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.probe_query:
        log(f"running probe query: {args.probe_query}")
        search_probe(
            client,
            args.collection,
            model,
            args.probe_query,
            args.probe_top_k,
            args.max_length,
            args.candidate_k,
            args.dense_weight,
            args.sparse_weight,
        )


def log(message: str) -> None:
    print(f"[ingest] {message}", flush=True)


def should_log_batch(batch_no: int, log_every: int, total_batches: int) -> bool:
    log_every = max(1, log_every)
    return batch_no == 1 or batch_no == total_batches or batch_no % log_every == 0


def load_chunks(input_dir: Path) -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(input_dir.rglob("*.jsonl")):
        if not path.name.endswith(".chunks.jsonl"):
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            chunk = json.loads(line)
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            search_text = str(chunk.get("search_text") or "").strip()
            answer_text = str(chunk.get("answer_text") or "").strip()
            if not chunk_id or not search_text or not answer_text:
                raise SystemExit(f"Invalid chunk at {path}:{line_no}")
            chunks.append(chunk)
    return chunks


def resolve_model_path(model_name: str) -> str:
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
        raise SystemExit("FlagEmbedding is not installed. Run: python -m pip install FlagEmbedding") from exc
    device = resolve_device(device_mode)
    fp16_enabled = bool(use_fp16 and device.startswith("cuda"))
    return BGEM3FlagModel(model_name, use_fp16=fp16_enabled, devices=device)


def resolve_device(device_mode: str) -> str:
    device_mode = (device_mode or "auto").strip().lower()
    try:
        import torch
    except Exception as exc:
        raise SystemExit("torch is not installed. Install a CUDA-enabled torch build for GPU embedding.") from exc
    cuda_available = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
    if device_mode == "auto":
        return "cuda:0" if cuda_available else "cpu"
    if device_mode == "cuda":
        if not cuda_available:
            raise SystemExit("CUDA is not available in this Python environment.")
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


def chunk_to_row(chunk: dict, dense_vector: list[float], sparse_vector: SparseVector) -> dict:
    metadata = dict(chunk.get("metadata") or {})
    metadata.setdefault("answer_text", str(chunk.get("answer_text") or ""))
    # Store both search_text and answer_text: search_text carries source hints, answer_text is cited to the LLM.
    return {
        "id": str(chunk["chunk_id"]),
        "vector": dense_vector,
        "sparse_vector": sparse_vector,
        "text": str(chunk["search_text"])[:32768],
        "answer_text": str(chunk["answer_text"])[:32768],
        "source_id": str(chunk.get("source_id") or metadata.get("source_id") or ""),
        "source_file": str(chunk.get("source_file") or metadata.get("source_file") or ""),
        "doc_type": str(chunk.get("doc_type") or metadata.get("doc_type") or ""),
        "section_path": str(chunk.get("section") or metadata.get("section_path") or ""),
        "chunk_index": int(chunk.get("chunk_index") or 0),
        "metadata": metadata,
    }


def ensure_collection(client, collection_name: str, vector_dim: int, recreate: bool) -> None:
    from pymilvus import DataType, MilvusClient

    if recreate and client.has_collection(collection_name):
        client.drop_collection(collection_name)

    if not client.has_collection(collection_name):
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=512)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=vector_dim)
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=32768)
        schema.add_field(field_name="answer_text", datatype=DataType.VARCHAR, max_length=32768)
        schema.add_field(field_name="source_id", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="source_file", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="section_path", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        schema.add_field(field_name="metadata", datatype=DataType.JSON, nullable=True)
        client.create_collection(collection_name=collection_name, schema=schema)

    required = {"id", "vector", "sparse_vector", "text", "answer_text", "source_id", "source_file", "doc_type", "section_path", "chunk_index", "metadata"}
    missing = required - field_names(client, collection_name)
    if missing:
        raise SystemExit(f"Collection {collection_name!r} is missing fields {sorted(missing)}. Use --recreate or a new collection name.")

    if not has_index(client, collection_name, "vector"):
        dense_index_params = MilvusClient.prepare_index_params()
        dense_index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_index(collection_name=collection_name, index_params=dense_index_params)

    if not has_index(client, collection_name, "sparse_vector"):
        sparse_index_params = MilvusClient.prepare_index_params()
        sparse_index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
            params={"drop_ratio_build": 0.2},
        )
        client.create_index(collection_name=collection_name, index_params=sparse_index_params)


def has_index(client, collection_name: str, field_name: str) -> bool:
    return bool(client.list_indexes(collection_name=collection_name, field_name=field_name))


def field_names(client, collection_name: str) -> set[str]:
    description = client.describe_collection(collection_name=collection_name)
    fields = description.get("fields") if isinstance(description, dict) else []
    names: set[str] = set()
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        name = field.get("name") or field.get("field_name")
        if name:
            names.add(str(name))
    return names


def chunked(items: list[dict], size: int) -> Iterable[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def search_probe(
    client,
    collection_name: str,
    model: object,
    query: str,
    top_k: int,
    max_length: int,
    candidate_k: int,
    dense_weight: float,
    sparse_weight: float,
) -> None:
    dense_vectors, sparse_vectors = encode_batch(model, [query], batch_size=1, max_length=max_length)
    client.load_collection(collection_name=collection_name)
    candidate_k = max(candidate_k, top_k)
    dense_hits = search_vector(client, collection_name, dense_vectors[0], "vector", "COSINE", candidate_k)
    sparse_hits = search_vector(client, collection_name, sparse_vectors[0], "sparse_vector", "IP", candidate_k)
    fused_hits = weighted_fuse(dense_hits, sparse_hits, top_k=top_k, dense_weight=dense_weight, sparse_weight=sparse_weight)
    print(
        json.dumps(
            {
                "probe_query": query,
                "top_k": top_k,
                "candidate_k_per_route": candidate_k,
                "dense_weight": dense_weight,
                "sparse_weight": sparse_weight,
                "dense_hits": len(dense_hits),
                "sparse_hits": len(sparse_hits),
                "hits": fused_hits,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def search_vector(client, collection_name: str, vector: list[float] | SparseVector, anns_field: str, metric_type: str, top_k: int) -> list[dict]:
    results = client.search(
        collection_name=collection_name,
        data=[vector],
        anns_field=anns_field,
        limit=top_k,
        output_fields=OUTPUT_FIELDS,
        search_params={"metric_type": metric_type},
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
                "source_file": entity.get("source_file"),
                "section_path": entity.get("section_path"),
                "answer_text": (entity.get("answer_text") or "")[:260],
            }
        )
    return payload


def weighted_fuse(dense_hits: list[dict], sparse_hits: list[dict], top_k: int, dense_weight: float, sparse_weight: float) -> list[dict]:
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
    return [{**payloads[hit_id], "weighted_score": score} for hit_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]]


def normalized_hit_scores(hits: list[dict]) -> list[tuple[dict, float]]:
    if not hits:
        return []
    raw_scores = [float(hit.get("score") or 0.0) for hit in hits]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    if max_score == min_score:
        return [(hit, 1.0) for hit in hits]
    return [(hit, (score - min_score) / (max_score - min_score)) for hit, score in zip(hits, raw_scores)]


if __name__ == "__main__":
    main()
