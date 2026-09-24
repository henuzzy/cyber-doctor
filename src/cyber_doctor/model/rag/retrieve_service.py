from typing import List

from cyber_doctor.model.rag.document import Document
from cyber_doctor.model.rag.medical_retriever import DEFAULT_TOP_K, INSTANCE


def retrieve(query: str, top_k: int = DEFAULT_TOP_K) -> List[Document]:
    return INSTANCE.retrieve(query, top_k=top_k)
