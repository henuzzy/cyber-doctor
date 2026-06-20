import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from config.config import Config
from langchain_community.document_loaders import (
    CSVLoader,
    DirectoryLoader,
    MHTMLLoader,
    PyPDFLoader,
    TextLoader,
)
from langchain_community.embeddings import ModelScopeEmbeddings
from langchain_community.vectorstores import Milvus
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from modelscope.hub.snapshot_download import snapshot_download

try:
    from langchain_community.document_loaders import (
        UnstructuredHTMLLoader,
        UnstructuredMarkdownLoader,
        UnstructuredWordDocumentLoader,
    )
except ImportError:
    UnstructuredHTMLLoader = None
    UnstructuredMarkdownLoader = None
    UnstructuredWordDocumentLoader = None


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".csv", ".html", ".mhtml", ".md"}


def get_config_value(default, *keys):
    try:
        return Config.get_instance().get_with_nested_params(*keys)
    except KeyError:
        return default


def get_knowledge_base_path() -> str:
    return Config.get_instance().get_with_nested_params("Knowledge-base-path")


def get_milvus_connection_args() -> dict:
    return {
        "host": str(get_config_value("localhost", "database", "milvus", "host")),
        "port": str(get_config_value("19530", "database", "milvus", "port")),
    }


def get_milvus_collection_name() -> str:
    return str(get_config_value("cyber_doctor_knowledge", "rag", "milvus", "collection-name"))


def get_manifest_path() -> str:
    return str(get_config_value("./data/vectorstore/milvus_manifest.json", "rag", "milvus", "manifest-path"))


def get_chunk_size() -> int:
    return int(get_config_value(2000, "rag", "chunk-size"))


def get_chunk_overlap() -> int:
    return int(get_config_value(100, "rag", "chunk-overlap"))


def get_search_top_k() -> int:
    return int(get_config_value(6, "rag", "search-top-k"))


def get_embedding_device() -> str:
    return str(get_config_value("cpu", "model", "embedding", "device"))


def get_embedding() -> ModelScopeEmbeddings:
    embedding_download_path = Config.get_instance().get_with_nested_params(
        "model", "embedding", "model-path"
    )
    embedding_model_name = Config.get_instance().get_with_nested_params(
        "model", "embedding", "model-name"
    )
    embedding_model_path = os.path.join(embedding_download_path, embedding_model_name)

    if not os.path.exists(embedding_model_path):
        snapshot_download(embedding_model_name, cache_dir=embedding_download_path)

    return ModelScopeEmbeddings(
        model_id=embedding_model_path,
        model_kwargs={"device": get_embedding_device()},
    )


def get_milvus_vectorstore(
    embedding=None,
    drop_old: bool = False,
    collection_name: str | None = None,
) -> Milvus:
    return Milvus(
        embedding_function=embedding or get_embedding(),
        collection_name=collection_name or get_milvus_collection_name(),
        connection_args=get_milvus_connection_args(),
        drop_old=drop_old,
        auto_id=False,
        primary_field="pk",
        text_field="text",
        vector_field="vector",
    )


def iter_knowledge_files(data_path: str) -> Iterable[Path]:
    root = Path(data_path)
    if not root.exists():
        return []
    return (
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_chunk_id(doc_hash: str, chunk_index: int) -> str:
    return f"{doc_hash}_{chunk_index}"


def load_manifest(path: str | None = None) -> dict:
    manifest_path = Path(path or get_manifest_path())
    if not manifest_path.exists():
        return {"documents": {}}
    with manifest_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_manifest(manifest: dict, path: str | None = None) -> None:
    manifest_path = Path(path or get_manifest_path())
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)


def load_documents_from_directory(data_path: str) -> list[Document]:
    docs: list[Document] = []
    docs.extend(_load_with_directory_loader(data_path, "**/*.pdf", PyPDFLoader))
    docs.extend(_load_optional_loader(data_path, "**/*.docx", UnstructuredWordDocumentLoader))
    docs.extend(
        _load_with_directory_loader(
            data_path,
            "**/*.txt",
            TextLoader,
            loader_kwargs={"autodetect_encoding": True},
        )
    )
    docs.extend(
        _load_with_directory_loader(
            data_path,
            "**/*.csv",
            CSVLoader,
            loader_kwargs={"autodetect_encoding": True},
        )
    )
    docs.extend(_load_optional_loader(data_path, "**/*.html", UnstructuredHTMLLoader))
    docs.extend(_load_with_directory_loader(data_path, "**/*.mhtml", MHTMLLoader))
    docs.extend(_load_markdown_docs(data_path))
    return docs


def load_documents_from_file(path: Path) -> list[Document]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PyPDFLoader(str(path)).load()
    if suffix == ".docx" and UnstructuredWordDocumentLoader is not None:
        return UnstructuredWordDocumentLoader(str(path)).load()
    if suffix == ".txt":
        return TextLoader(str(path), autodetect_encoding=True).load()
    if suffix == ".csv":
        return CSVLoader(str(path), autodetect_encoding=True).load()
    if suffix == ".html" and UnstructuredHTMLLoader is not None:
        return UnstructuredHTMLLoader(str(path)).load()
    if suffix == ".mhtml":
        return MHTMLLoader(str(path)).load()
    if suffix == ".md":
        if UnstructuredMarkdownLoader is not None:
            return UnstructuredMarkdownLoader(str(path)).load()
        return TextLoader(str(path), encoding="utf-8").load()
    return []


def split_documents(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=get_chunk_size(),
        chunk_overlap=get_chunk_overlap(),
    )
    return splitter.split_documents(docs)


def prepare_chunks_for_file(path: Path, data_root: Path, doc_hash: str) -> list[Document]:
    docs = load_documents_from_file(path)
    rel_path = path.relative_to(data_root).as_posix()
    prepared_docs = []
    for doc in docs:
        metadata = dict(doc.metadata or {})
        source = metadata.get("source") or str(path)
        page = metadata.get("page", metadata.get("page_number", -1))
        metadata.update(
            {
                "source": source,
                "doc_path": rel_path,
                "doc_name": path.name,
                "doc_hash": doc_hash,
                "page": int(page) if isinstance(page, int) or str(page).isdigit() else -1,
            }
        )
        prepared_docs.append(Document(page_content=doc.page_content, metadata=metadata))

    chunks = split_documents(prepared_docs)
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = index
        chunk.metadata["chunk_id"] = stable_chunk_id(doc_hash, index)
    return chunks


def delete_document_chunks(vectorstore: Milvus, doc_path: str) -> None:
    if vectorstore.col is None:
        return
    safe_path = doc_path.replace("\\", "\\\\").replace('"', '\\"')
    vectorstore.col.delete(expr=f'doc_path == "{safe_path}"')
    vectorstore.col.flush()


def ingest_directory_to_milvus(rebuild: bool = False, check_hash: bool = False) -> dict:
    data_path = Path(get_knowledge_base_path())
    manifest = {"documents": {}} if rebuild else load_manifest()
    embedding = get_embedding()
    vectorstore = get_milvus_vectorstore(embedding=embedding, drop_old=rebuild)

    stats = {"inserted_files": 0, "skipped_files": 0, "inserted_chunks": 0}
    for path in iter_knowledge_files(str(data_path)):
        rel_path = path.relative_to(data_path).as_posix()
        stat = path.stat()
        previous = manifest["documents"].get(rel_path)

        if (
            previous
            and not check_hash
            and previous.get("size") == stat.st_size
            and previous.get("mtime_ns") == stat.st_mtime_ns
        ):
            stats["skipped_files"] += 1
            print(f"Skip unchanged file: {rel_path}")
            continue

        doc_hash = sha256_file(path)
        if previous and previous.get("content_hash") == doc_hash:
            previous["size"] = stat.st_size
            previous["mtime_ns"] = stat.st_mtime_ns
            stats["skipped_files"] += 1
            print(f"Skip already embedded file: {rel_path}")
            continue

        if previous:
            delete_document_chunks(vectorstore, rel_path)

        chunks = prepare_chunks_for_file(path, data_path, doc_hash)
        if not chunks:
            print(f"No chunks loaded from file: {rel_path}")
            continue

        ids = [chunk.metadata["chunk_id"] for chunk in chunks]
        vectorstore.add_documents(chunks, ids=ids)
        manifest["documents"][rel_path] = {
            "content_hash": doc_hash,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "chunk_count": len(chunks),
            "collection": get_milvus_collection_name(),
            "embedded_at": datetime.now(timezone.utc).isoformat(),
        }
        save_manifest(manifest)

        stats["inserted_files"] += 1
        stats["inserted_chunks"] += len(chunks)
        print(f"Inserted {len(chunks)} chunks from {rel_path}")

    save_manifest(manifest)
    return stats


def _load_with_directory_loader(data_path: str, glob: str, loader_cls, loader_kwargs=None):
    loader = DirectoryLoader(
        data_path,
        glob=glob,
        loader_cls=loader_cls,
        silent_errors=True,
        loader_kwargs=loader_kwargs or {},
        use_multithreading=True,
    )
    return loader.load()


def _load_optional_loader(data_path: str, glob: str, loader_cls):
    if loader_cls is None:
        return []
    return _load_with_directory_loader(data_path, glob, loader_cls)


def _load_markdown_docs(data_path: str):
    if UnstructuredMarkdownLoader is None:
        return _load_with_directory_loader(
            data_path,
            "**/*.md",
            TextLoader,
            loader_kwargs={"encoding": "utf-8"},
        )
    return _load_with_directory_loader(data_path, "**/*.md", UnstructuredMarkdownLoader)
