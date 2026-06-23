'''问答类型判断函数，根据特定输入和大模型进行分类分类。'''
from typing import List, Dict

from client.clientfactory import Clientfactory

from qa.prompt_templates import get_question_parser_prompt
from qa.purpose_type import purpose_map
from qa.purpose_type import userPurposeType

from icecream import ic


def is_mngs_judgement_question(question: str) -> bool:
    """Detect the mNGS pathogen harmful/harmless judgement task."""
    if not question:
        return False

    direct_markers = (
        "单个mNGS检出病原",
        "mNGS检出病原",
        "mNGS结果解读",
        "当前患者是否有害",
    )
    if any(marker in question for marker in direct_markers):
        return True

    judgement_markers = ("有害", "无害", "Label", "OriginalFlag")
    mngs_markers = ("mNGS", "病原", "检出", "覆盖率", "丰度", "取样部位", "检测组织")
    case_markers = ("病原基本信息", "患者信息", "免疫状态", "临床表型", "医生诊断")
    structured_markers = ("messages", "Label", "OriginalFlag", "Latin", "Chinese", "UUID")

    has_judgement = any(marker in question for marker in judgement_markers)
    has_mngs = "mNGS" in question and any(marker in question for marker in mngs_markers)
    has_case = sum(1 for marker in case_markers if marker in question) >= 2
    has_structured_mngs = "mNGS" in question and sum(1 for marker in structured_markers if marker in question) >= 2
    return has_judgement and (has_mngs or has_case or has_structured_mngs)


def parse_question(question: str, image_url=None) -> userPurposeType:

    if is_mngs_judgement_question(question):
        return purpose_map["mNGS判别"]

    if "根据知识库" in question:
        return purpose_map["基于知识库"]
    
    if "根据知识图谱" in question:
        return purpose_map["基于知识图谱"]

    if "搜索" in question:
        return purpose_map["网络搜索"]
    
    if image_url is not None:
        return purpose_map["图片描述"]

    # 在这个函数中我们使用大模型去判断问题类型
    prompt = get_question_parser_prompt(question)
    response = Clientfactory().get_client().chat_with_ai(prompt)
    ic("大模型分类结果：" + response)

    if response == "图片生成" and len(question) > 0:
        return purpose_map["图片生成"]
    if response == "文本生成":
        return purpose_map["文本生成"]
    return purpose_map["其他"]



