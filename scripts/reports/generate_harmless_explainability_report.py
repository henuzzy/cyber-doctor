#!/usr/bin/env python3
"""Generate three evidence-bounded explanations with the DGX Qwen model."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

INPUT = Path(r"E:\华大医疗agent资料\待判断的病原.md")
KNOWLEDGE_ROOT = Path(r"E:\华大医疗agent资料\前20个病原的文章-uptodate-pubmed\结构化文档")
REFERENCE_FOLDERS = (
    "0001_id_00099539_BAC_S_1894172_",
    "0013_id_00733496_BAC_S_2626034_",
)
OUTPUT = KNOWLEDGE_ROOT / "报告对比_Qwen3.8-27b_基于GPT-5.6-Terra结构化文档" / "三个无害病原_解释内容_Qwen3.8-27b.json"
API = "http://10.8.0.22:8000/v1/chat/completions"
MODEL = "qwen3.8-27b"

EXTRA_REFERENCE = (
    "id|00099539|BAC|S_1894172|,该菌是一种不产芽孢、抗酸、暗产色的缓慢生长分枝杆菌，"
    "从韩国一名肺部感染患者的痰中分离出来 [1]，也曾引起一位腹膜透析病人的腹膜炎 [2]。"
)

SCHEMA = {
    "报告标题": "可解释性诊断报告",
    "总体说明": "",
    "病例": [
        {
            "UUID": "",
            "病原中文名": "",
            "病原拉丁名": "",
            "既有结论": "无害",
            "解释置信度": "高/中/低",
            "患者摘要": "",
            "mNGS证据": [],
            "临床证据匹配": "",
            "致病性参考": [],
            "无害结论解释": "",
            "证据局限": [],
            "建议复核项": [],
        }
    ],
}


def load_cases() -> list[dict]:
    cases = []
    for line in INPUT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def read_json(path: Path) -> dict:
    """Read long Windows article paths without changing the source files."""
    long_path = Path("\\\\?\\" + str(path)) if len(str(path)) >= 248 else path
    return json.loads(long_path.read_text(encoding="utf-8"))


def load_knowledge() -> list[dict]:
    knowledge = []
    for folder_name in REFERENCE_FOLDERS:
        folder = KNOWLEDGE_ROOT / folder_name
        for source in ("PubMed", "UpToDate"):
            for path in sorted((folder / source).glob("*.json")):
                item = read_json(path)
                item["__reference_file__"] = str(path)
                knowledge.append(item)
    if not knowledge:
        raise RuntimeError("No current structured reference documents were found")
    return knowledge


def main() -> None:
    cases = load_cases()
    knowledge = load_knowledge()
    system = """你是临床感染病学和mNGS可解释性报告专家。任务不是重新分类，而是解释输入中已经确定的assistant标签“无害”。只能使用输入材料，禁止外部医学常识和杜撰文献。

每项表述都必须明确属于下列四种证据层级之一：病例直接证据、病例自带病原资料、同类分枝杆菌参考、更大NTM群参考。当前结构化文档中的副戈登分枝杆菌和杏林分枝杆菌都仅为同类分枝杆菌参考；其中UpToDate可能仅描述更大的NTM群。绝不能将这些资料改写成东海分枝杆菌、新金色分枝杆菌或伊朗分枝杆菌的直接证据，也不能从任何同类个案推导目标物种的感染部位、序列阈值、覆盖率阈值、治疗方案或致病因果。

不得给出任何通用阈值或外部判断规则：不得出现“通常认为”“可靠检出阈值”“高/低于阈值”“基因组覆盖不完整”“整体排名较低”“非主要病原”或任何含“污染”的表述。病例数据没有给出阈值时，只能陈述数值及“本次输入未提供判读截点/培养或靶向验证结果”。不能把当前输入没有提供的目标物种病例，概括为医学上“缺乏该病原的直接病例证据”；应准确写成“本次输入未提供该关联的直接验证证据”。

不要因患者免疫抑制或病原曾有致病报道就自动推翻“无害”标签；解释应重点分析本次检出的序列数、覆盖率、丰度、排名、取样部位、临床表型特异性和缺失的验证证据。不得给出启动或停止具体药物或抗结核治疗的建议。输出一个合法JSON对象，不要代码围栏。"""
    prompt = f"""请为以下3个已判定为“无害”的病原病例生成中文可解释性报告内容。

固定输出schema：
{json.dumps(SCHEMA, ensure_ascii=False)}

病例原始记录：
{json.dumps(cases, ensure_ascii=False)}

指定同类参考信息：
{EXTRA_REFERENCE}

本次结构化文档（仅同类分枝杆菌或更大NTM群参考，禁止直接外推；每条带有__reference_file__以便追溯）：
{json.dumps(knowledge, ensure_ascii=False)}

要求：
1. 严格输出3个病例，顺序与输入一致，既有结论均为“无害”。
2. 置信度表示对“无害解释”的证据充分度，不是病原检出可信度。
3. 每个事实均能追溯到输入；在“致病性参考”中显式标示其证据层级。每例必须有一条【病例自带病原资料】，准确复述该病例原始记录已有的目标物种历史致病描述；可另外列同类或更大NTM群参考。不可写“本次输入未提供目标物种直接病例证据”，因为原始记录已经给出历史病原资料；如需说明缺失，只能写“本次患者未提供该历史报道与当前患者之间的直接验证证据”。不生成输入中没有的治疗建议或诊断事实。
4. 复核建议仅可写成条件性证据需求，且只能从以下四类中选择：重复mNGS、培养或靶向检测、取样部位/元数据核对、临床表型相关性核对。不得写尿液常规、影像、排除污染、治疗、用药或其他输入外检查。
5. 语言专业、简洁、适合患者报告；每个数组2-4条，每条不超过80字。"""
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        API,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1200) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"].strip()
    start = content.find("{")
    result, _ = json.JSONDecoder().raw_decode(content[start:])
    if len(result.get("病例", [])) != 3:
        raise RuntimeError("DGX response did not contain exactly three cases")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT.resolve())


if __name__ == "__main__":
    main()
