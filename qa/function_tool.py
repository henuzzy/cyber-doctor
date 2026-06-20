'''存放处理不同问答类型的工具函数，核心文件'''

import base64
from typing import Callable, List, Dict, Tuple
from client.clientfactory import Clientfactory
from qa.purpose_type import userPurposeType
from pathlib import Path
from model.KG.search_service import search
from kg.Graph import GraphDao
from config.config import Config
from qa.purpose_type import userPurposeType
from env import get_env_value


_dao = GraphDao()

def is_file_path(path):
    return Path(path).exists()

def relation_tool(entities: List[Dict] | None) -> str | None:
    if not entities or len(entities) == 0:
        return None

    relationships = set()  # 使用集合来避免重复关系
    relationship_match = []

    searchKey = Config.get_instance().get_with_nested_params("model", "graph-entity", "search-key")
    # 遍历每个实体并查询与其他实体的关系
    for entity in entities:
        entity_name = entity[searchKey]
        for k, v in entity.items():
            relationships.add(f"{entity_name} {k}: {v}")

        # 查询每个实体与其他实体的关系a-r-b
        relationship_match.append(_dao.query_relationship_by_name(entity_name))
        
    # 抽取并记录每个实体与其他实体的关系
    for i in range(len(relationship_match)):
        for record in relationship_match[i]:
            # 获取起始节点和结束节点的名称

            start_name = record["r"].start_node[searchKey]
            end_name = record["r"].end_node[searchKey]

            # 获取关系类型
            rel = type(record["r"]).__name__  # 获取关系名称，比如 CAUSES

            # 构建关系字符串并添加到集合，确保不会重复添加
            relationships.add(f"{start_name} {rel} {end_name}")

    # 返回关系集合的内容
    if relationships:
        return "；".join(relationships)
    else:
        return None


def check_entity(question: str) -> List[Dict]:
    code, result = search(question)
    if code == 0:
        return result
    else:
        return None


def KG_tool(
    question_type: userPurposeType,
    question: str,
    history: List[List | None] = None,
    image_url=None,
):
    kg_info = None
    try:
        # 此处在使用知识图谱之前，需先检查问题的实体
        entities = check_entity(question)
        kg_info = relation_tool(entities)
    except:
        pass

    if kg_info is not None:
        print(f"KG_tool: \n {kg_info}")
        question = f"{question}\n从知识图谱中检索到的信息如下{kg_info}\n请你基于知识图谱的信息去回答,并给出知识图谱检索到的信息"

    response = Clientfactory().get_client().chat_with_ai_stream(question, history)
    return (response, question_type)


# 处理text问题的函数
def process_text_tool(
    question_type: userPurposeType,
    question: str,
    history: List[List | None] = None,
    image_url=None,
):
    response = Clientfactory().get_client().chat_with_ai_stream(question, history)
    return (response, question_type)


# 处理RAG问题
def RAG_tool(
    question_type: userPurposeType,
    question: str,
    history: List[List | None] = None,
    image_url=None,
):
    # 先利用question去检索得到docs
    from rag import rag_chain

    response = rag_chain.invoke(question, history)
    return (response, question_type)


# 处理ImageGeneration问题的函数
def process_images_tool(question_type, question, history, image_url=None):
    client = Clientfactory.get_special_client(client_type=question_type)
    response = client.images.generations(
        model=get_env_value("IMAGE_GENERATE_MODEL"),  # 填写需要调用的模型编码
        prompt=question,
    )
    print(response.data[0].url)
    return (response.data[0].url, question_type)


def process_image_describe_tool(question_type, question, history, image_url=None):
    if question == "请你将下面的句子修饰后输出，不要包含额外的文字，句子:'请问您有什么想了解的，我将尽力为您服务'":
        question = "描述这个图片，说明这个图片的主要内容"
    image_bases = []
    for img_url in image_url:
        if is_file_path(img_url):
            with open(img_url, "rb") as img_file:
                image_base = base64.b64encode(img_file.read()).decode("utf-8")
                image_bases.append(image_base)
        else:
            image_bases.append(img_url)

    # 构建 messages 内容
    message_content = []
    for image_base in image_bases:
        message_content.append({"type": "image_url", "image_url": {"url": image_base}})
    # 添加问题的文本内容
    message_content.append({"type": "text", "text": question})

    client = Clientfactory.get_special_client(client_type=question_type)
    # 发送请求
    response = client.chat.completions.create(
        model=get_env_value("IMAGE_DESCRIBE_MODEL"),
        messages=[
            {
                "role": "user",
                "content": message_content,
            }
        ],
    )
    return (response.choices[0].message.content, question_type)


# 处理联网搜索问题的函数
def process_InternetSearch_tool(
    question_type: userPurposeType,
    question: str,
    history: List[List | None] = None,
    image_url=None,
):
    from Internet.Internet_chain import InternetSearchChain

    response, links, success = InternetSearchChain(question, history)
    return (response, question_type, links, success)


QUESTION_TO_FUNCTION = {
    userPurposeType.text: process_text_tool,
    userPurposeType.RAG: RAG_tool,
    userPurposeType.ImageGeneration: process_images_tool,
    userPurposeType.InternetSearch: process_InternetSearch_tool,
    userPurposeType.ImageDescribe: process_image_describe_tool,
    userPurposeType.KnowledgeGraph: KG_tool,
}


# 根据用户不同的意图选择不同的函数
def map_question_to_function(purpose: userPurposeType) -> Callable:
    if purpose in QUESTION_TO_FUNCTION:
        return QUESTION_TO_FUNCTION[purpose]
    else:
        raise ValueError("没有找到意图对应的函数")
