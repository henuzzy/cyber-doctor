from typing import List

from model.RAG.document import Document
from model.RAG.medical_retriever import DEFAULT_TOP_K, INSTANCE


def retrieve(query: str, top_k: int = DEFAULT_TOP_K) -> List[Document]:
    return INSTANCE.retrieve(query, top_k=top_k)
