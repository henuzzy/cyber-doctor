from typing import List

from model.RAG.document import Document
from model.RAG.medical_retriever import INSTANCE


def retrieve(query: str) -> List[Document]:
    return INSTANCE.retrieve(query)
