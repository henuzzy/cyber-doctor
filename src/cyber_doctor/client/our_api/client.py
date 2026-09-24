from openai import OpenAI
from cyber_doctor.client.llm_client_generic import LLMClientGeneric

class OurAPI(LLMClientGeneric):
    def __init__(self,*args,**krgs):
        super().__init__(*args,**krgs)
