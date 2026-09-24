from typing import Tuple,List,Dict
from cyber_doctor.model.kg.search_model import INSTANCE

def search(query:str) -> Tuple[int,List[Dict]|None]:
    result = INSTANCE.search(query)
    if result is not None:
        return 0 , result
    else:
        return -1 , None
    
