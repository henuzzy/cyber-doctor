"""Milvus-backed retriever for the local knowledge base."""

from langchain_core.vectorstores import VectorStoreRetriever

from model.model_base import ModelStatus
from model.model_base import Modelbase
from model.RAG.milvus_store import get_embedding, get_milvus_vectorstore, get_search_top_k


class Retrievemodel(Modelbase):
    _retriever: VectorStoreRetriever

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._embedding = get_embedding()
        self._retriever = None

    def build(self):
        vectorstore = get_milvus_vectorstore(embedding=self._embedding)
        self._retriever = vectorstore.as_retriever(search_kwargs={"k": get_search_top_k()})
        self._model_status = ModelStatus.READY

    @property
    def retriever(self) -> VectorStoreRetriever:
        if self._retriever is None or self._model_status == ModelStatus.FAILED:
            self.build()
        return self._retriever


INSTANCE = Retrievemodel()
