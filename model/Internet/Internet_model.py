"""Milvus-backed temporary retriever for internet search results."""

import os

from env import get_app_root
from langchain_community.document_loaders import DirectoryLoader, MHTMLLoader, UnstructuredHTMLLoader
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter
from model.RAG.milvus_store import (
    get_config_value,
    get_embedding,
    get_milvus_vectorstore,
    get_search_top_k,
)
from model.model_base import Modelbase, ModelStatus


class InternetModel(Modelbase):
    _retriever: VectorStoreRetriever

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._embedding = get_embedding()
        self._data_path = os.path.join(get_app_root(), "data/cache/internet")
        self._collection_name = str(
            get_config_value(
                "cyber_doctor_internet_cache",
                "rag",
                "milvus",
                "internet-collection-name",
            )
        )

    def build(self):
        html_loader = DirectoryLoader(
            self._data_path,
            glob="**/*.html",
            loader_cls=UnstructuredHTMLLoader,
            silent_errors=True,
            use_multithreading=True,
        )
        html_docs = html_loader.load()

        mhtml_loader = DirectoryLoader(
            self._data_path,
            glob="**/*.mhtml",
            loader_cls=MHTMLLoader,
            silent_errors=True,
            use_multithreading=True,
        )
        mhtml_docs = mhtml_loader.load()
        docs = html_docs + mhtml_docs

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=int(get_config_value(2000, "rag", "chunk-size")),
            chunk_overlap=int(get_config_value(100, "rag", "chunk-overlap")),
        )
        splits = text_splitter.split_documents(docs)

        vectorstore = get_milvus_vectorstore(
            embedding=self._embedding,
            drop_old=True,
            collection_name=self._collection_name,
        )
        if splits:
            ids = [f"internet_{index}" for index in range(len(splits))]
            vectorstore.add_documents(splits, ids=ids)

        self._retriever = vectorstore.as_retriever(search_kwargs={"k": get_search_top_k()})
        self._model_status = ModelStatus.READY

    @property
    def retriever(self) -> VectorStoreRetriever:
        self.build()
        return self._retriever


INSTANCE = InternetModel()
