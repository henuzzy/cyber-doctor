from typing import List,Tuple
from cyber_doctor.model.rag.document import Document
from cyber_doctor.model.rag.retrieve_service import retrieve

def format_docs(docs:List[Document]):
    return "\n-------------分割线--------------\n".join(doc.page_content for doc in docs)

def retrieve_docs(question:str)->Tuple[List[Document],str]:
    docs = retrieve(question) # 这里的到的是文件
    _context = format_docs(docs) # 这里处理成文本
    print(_context)
    return (docs,_context)
    
