from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List

from model.RAG.document import Document


CATALOG_PATH = Path(__file__).resolve().with_name("pathogen_catalog.json")


@dataclass
class MNGSCase:
    pathogen_type: str = ""
    species_latin: str = ""
    species_chinese: str = ""
    genus_latin: str = ""
    genus_chinese: str = ""
    sample_type: str = ""
    age: str = ""
    sex: str = ""
    phenotype: str = ""
    diagnosis: str = ""
    immune_status: str = ""
    reads: str = ""
    genus_reads: str = ""
    coverage: str = ""
    abundance: str = ""
    genus_abundance: str = ""
    genus_rank: str = ""
    species_rank: str = ""
    pathogenicity_text: str = ""
    raw_question: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class EvidenceBundle:
    queries: list[str] = field(default_factory=list)
    docs: list[Document] = field(default_factory=list)
    context: str = ""


def build_mngs_rag_prompt(question: str) -> tuple[str, EvidenceBundle]:
    case = parse_mngs_case(question)
    evidence = retrieve_mngs_evidence(case)
    prompt = build_judge_prompt(case, evidence)
    return prompt, evidence


def judge_with_rag_stream(question: str, history: List[List | None] | None = None):
    from client.clientfactory import Clientfactory

    prompt, _ = build_mngs_rag_prompt(question)
    return Clientfactory().get_client().chat_with_ai_stream(prompt, history)


def parse_mngs_case(question: str) -> MNGSCase:
    case = MNGSCase(raw_question=question)
    payload = _parse_loose_json_object(question)
    working_text = _expand_messages_text(question, payload)

    case.species_latin = _clean_taxon(_str(payload.get("Latin") or _quoted_field(question, "Latin")))
    case.species_chinese = _str(payload.get("Chinese") or _quoted_field(question, "Chinese"))
    case.pathogen_type = _str(payload.get("病原类型") or _quoted_field(question, "病原类型"))
    case.sample_type = _str(
        payload.get("取样部位_from_prompt")
        or payload.get("取样部位_raw_metadata")
        or _quoted_field(question, "取样部位_from_prompt")
        or _quoted_field(question, "取样部位_raw_metadata")
    )
    case.immune_status = _normalize_immune_status(payload.get("mianyi"))

    pathogen_info = _extract_dict_after(working_text, "病原基本信息")
    if pathogen_info:
        case.pathogen_type = _str(pathogen_info.get("类型")) or case.pathogen_type
        case.species_latin = _clean_taxon(_str(pathogen_info.get("种-拉丁名"))) or case.species_latin
        case.species_chinese = _str(pathogen_info.get("种-中文名")) or case.species_chinese
        case.genus_latin = _clean_taxon(_str(pathogen_info.get("属-拉丁名")))
        case.genus_chinese = _str(pathogen_info.get("属-中文名"))
    case.pathogen_type = case.pathogen_type or _dict_like_field(working_text, "类型")
    case.species_latin = case.species_latin or _clean_taxon(_dict_like_field(working_text, "种-拉丁名"))
    case.species_chinese = case.species_chinese or _dict_like_field(working_text, "种-中文名")
    case.genus_latin = case.genus_latin or _clean_taxon(_dict_like_field(working_text, "属-拉丁名"))
    case.genus_chinese = case.genus_chinese or _dict_like_field(working_text, "属-中文名")

    patient_info = _extract_dict_after(working_text, "患者信息为")
    if patient_info:
        case.age = _str(patient_info.get("年龄"))
        case.sex = _str(patient_info.get("性别"))
        case.phenotype = _str(patient_info.get("临床表型"))
        case.diagnosis = _str(patient_info.get("医生诊断"))
    case.age = case.age or _dict_like_field(working_text, "年龄")
    case.sex = case.sex or _dict_like_field(working_text, "性别")
    case.phenotype = case.phenotype or _dict_like_field(working_text, "临床表型")
    case.diagnosis = case.diagnosis or _dict_like_field(working_text, "医生诊断")

    case.reads = _search_value(working_text, r"种-检出序列数：([^。\n]+)")
    case.genus_reads = _search_value(working_text, r"属-检出序列数为([^。\n]+)")
    case.coverage = _search_value(working_text, r"覆盖率：([^。\n]+)")
    case.abundance = _search_value(working_text, r"种-丰度：([^。\n]+)")
    case.genus_abundance = _search_value(working_text, r"属-丰度为([^。\n]+)")
    case.genus_rank = _search_value(working_text, r"属的排序为：([^，。\n]+)") or _str(payload.get("属排名"))
    case.species_rank = _search_value(working_text, r"种的排序为：([^，。\n]+)") or _str(payload.get("种排名"))
    case.sample_type = _search_value(working_text, r"mNGS检测组织为：([^，。\n]+)") or case.sample_type
    case.immune_status = _search_value(working_text, r"患者的免疫状态为：([^，。\n]+)") or case.immune_status
    case.pathogenicity_text = _search_value(working_text, r"病原的致病信息为：(.+?)(?:\n\n|输出要求|$)", flags=re.S)
    apply_mngs_fallbacks(case, working_text)
    enrich_case_from_catalog(case)

    return case


def apply_mngs_fallbacks(case: MNGSCase, text: str) -> None:
    if not case.species_latin:
        latin_candidates = re.findall(r"\b[A-Z][a-z]+_[a-z][A-Za-z_]+\b", text)
        if latin_candidates:
            case.species_latin = latin_candidates[0]
    if not case.genus_latin and case.species_latin and "_" in case.species_latin:
        case.genus_latin = case.species_latin.split("_", 1)[0]

    if not case.species_chinese:
        chinese_match = re.search(r"种-中文名['\"]?\s*[:：]\s*['\"]([^'\"，。}]+)", text)
        if chinese_match:
            case.species_chinese = chinese_match.group(1).strip()
    if not case.genus_chinese:
        genus_match = re.search(r"属-中文名['\"]?\s*[:：]\s*['\"]([^'\"，。}]+)", text)
        if genus_match:
            case.genus_chinese = genus_match.group(1).strip()

    if not case.sample_type:
        sample_match = re.search(r"mNGS检测组织为：([^，。\n]+)", text)
        if sample_match:
            case.sample_type = sample_match.group(1).strip()
    if not case.phenotype:
        phenotype_match = re.search(r"临床表型['\"]?\s*[:：]\s*['\"]([^'\"，。}]+)", text)
        if phenotype_match:
            case.phenotype = phenotype_match.group(1).strip()


def enrich_case_from_catalog(case: MNGSCase) -> None:
    record = lookup_pathogen_record(case)
    if record:
        case.pathogen_type = case.pathogen_type or _str(record.get("type"))
        case.species_latin = case.species_latin or _str(record.get("species_latin"))
        case.species_chinese = case.species_chinese or _str(record.get("species_chinese"))
        case.genus_latin = case.genus_latin or _str(record.get("genus_latin"))
        case.genus_chinese = case.genus_chinese or _str(record.get("genus_chinese"))
        case.aliases = _unique(record.get("aliases") or [])

    case.aliases = _unique(
        [
            *case.aliases,
            case.species_latin,
            case.species_latin.replace("_", " ") if case.species_latin else "",
            case.species_chinese,
            case.genus_latin,
            case.genus_latin.replace("_", " ") if case.genus_latin else "",
            case.genus_chinese,
        ]
    )


def lookup_pathogen_record(case: MNGSCase) -> dict:
    catalog = load_pathogen_catalog()
    keys = _unique(
        [
            case.species_latin,
            case.species_latin.replace("_", " ") if case.species_latin else "",
            case.species_chinese,
            case.genus_latin,
            case.genus_chinese,
        ]
    )
    for key in keys:
        record = catalog.get(key.lower())
        if record:
            return record
    return {}


@lru_cache(maxsize=1)
def load_pathogen_catalog() -> dict[str, dict]:
    if not CATALOG_PATH.exists():
        return {}
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for record in data.get("pathogens", []):
        keys = [
            record.get("species_latin"),
            record.get("species_latin_space"),
            record.get("species_chinese"),
            record.get("genus_latin"),
            record.get("genus_latin_space"),
            record.get("genus_chinese"),
            *(record.get("aliases") or []),
        ]
        for key in keys:
            cleaned = _squash(key).lower()
            if cleaned:
                index.setdefault(cleaned, record)
    return index


def retrieve_mngs_evidence(case: MNGSCase) -> EvidenceBundle:
    queries = build_mngs_queries(case)
    try:
        from model.RAG.retrieve_service import retrieve
    except Exception as exc:
        print(f"mNGS RAG retriever is unavailable: {exc}")
        return EvidenceBundle(queries=queries, docs=[], context=format_evidence_docs([]))

    docs_by_key: dict[str, Document] = {}
    for query in queries:
        try:
            # Multiple clinical/taxon queries broaden recall; de-dup keeps the prompt evidence compact.
            for doc in retrieve(query):
                key = _doc_key(doc)
                if key not in docs_by_key:
                    docs_by_key[key] = doc
        except Exception as exc:
            print(f"mNGS RAG retrieve failed for query {query!r}: {exc}")

    docs = rank_mngs_docs(case, list(docs_by_key.values()))
    return EvidenceBundle(queries=queries, docs=docs, context=format_evidence_docs(docs))


def rank_mngs_docs(case: MNGSCase, docs: list[Document]) -> list[Document]:
    # Retrieval is broad by design; boost chunks that mention the exact pathogen,
    # genus, sample site, or phenotype so the prompt sees disease-specific evidence first.
    terms = _unique(
        [
            *case.aliases,
            case.species_chinese,
            case.species_latin,
            case.species_latin.replace("_", " ") if case.species_latin else "",
            case.genus_chinese,
            case.genus_latin,
            case.genus_latin.replace("_", " ") if case.genus_latin else "",
            case.sample_type,
            case.phenotype,
            case.diagnosis,
        ]
    )

    def score(doc: Document) -> float:
        metadata = doc.metadata or {}
        haystack = " ".join(
            [
                doc.page_content or "",
                str(metadata.get("source_file") or ""),
                str(metadata.get("section_path") or metadata.get("section") or ""),
            ]
        ).lower()
        base = float(metadata.get("weighted_score") or metadata.get("score") or 0.0)
        boost = 0.0
        for term in terms:
            normalized = term.lower()
            if normalized and normalized in haystack:
                boost += 2.0 if term in (case.species_chinese, case.species_latin) else 0.6
        return base + boost

    return sorted(docs, key=score, reverse=True)


def build_mngs_queries(case: MNGSCase) -> list[str]:
    queries: list[str] = []
    species_names = species_query_names(case)
    genus_names = genus_query_names(case)

    # Rule-based query rewriting keeps this task deterministic: one case becomes
    # pathogen, genus, sample-site, phenotype, and low-confidence mNGS evidence routes.
    for name in species_names:
        queries.append(f"{name} 致病性 感染 临床表现 感染部位")
        queries.append(f"{name} 定植 背景病原 污染 mNGS")
        queries.append(f"{name} case report infection clinical manifestation")
        if case.sample_type:
            queries.append(f"{name} {case.sample_type} 感染 {site_infection_terms(case.sample_type)}")
        if case.phenotype:
            queries.append(f"{name} {case.phenotype} 临床表型 症状")

    for name in genus_names:
        queries.append(f"{name} 感染 临床表现 感染部位")
        if case.sample_type:
            queries.append(f"{name} {case.sample_type} 感染 定植")

    if case.sample_type or case.phenotype:
        queries.append(f"{case.sample_type} {case.phenotype} 常见病原 感染 鉴别诊断")
        queries.append(f"{case.sample_type} mNGS 背景菌 污染 定植 低序列数 低覆盖率")
    if case.diagnosis:
        queries.append(f"{case.diagnosis} {case.sample_type} {case.phenotype} 感染 常见病原")

    if case.pathogenicity_text:
        queries.append(case.pathogenicity_text[:220])

    if low_confidence_detection(case):
        primary = species_names[0] if species_names else case.species_chinese or case.species_latin
        queries.append(f"{primary} 低序列数 低覆盖率 低丰度 mNGS 污染 背景")

    fallback = " ".join([*species_names[:3], *genus_names[:2], case.sample_type, case.phenotype, case.diagnosis, case.immune_status, "mNGS 有害 无害 判断"])
    queries.append(fallback.strip())
    return _unique(query for query in queries if query and query.strip())[:18]


def species_query_names(case: MNGSCase) -> list[str]:
    names = [
        case.species_chinese,
        case.species_latin.replace("_", " ") if case.species_latin else "",
        case.species_latin,
    ]
    for alias in case.aliases:
        if alias in (case.genus_latin, case.genus_chinese, case.genus_latin.replace("_", " ") if case.genus_latin else ""):
            continue
        names.append(alias)
    return _unique(names)[:4]


def genus_query_names(case: MNGSCase) -> list[str]:
    return _unique([case.genus_chinese, case.genus_latin.replace("_", " ") if case.genus_latin else "", case.genus_latin])[:3]


def site_infection_terms(sample_type: str) -> str:
    if any(term in sample_type for term in ("尿", "尿液")):
        return "尿路感染 泌尿系统感染"
    if any(term in sample_type for term in ("肺", "痰", "支气管", "肺泡", "灌洗")):
        return "肺部感染 呼吸道感染 肺炎"
    if any(term in sample_type for term in ("血", "血液", "血浆")):
        return "血流感染 菌血症 败血症"
    if any(term in sample_type for term in ("脑脊液", "脑")):
        return "中枢神经系统感染 脑膜炎"
    if any(term in sample_type for term in ("粪", "便", "肠")):
        return "肠道感染 腹泻"
    return "感染部位"


def low_confidence_detection(case: MNGSCase) -> bool:
    reads = _safe_float(case.reads)
    coverage = _safe_float(case.coverage)
    abundance = _safe_float(case.abundance)
    return (reads > 0 and reads <= 3) or (coverage > 0 and coverage < 0.1) or abundance == 0


def format_evidence_docs(docs: list[Document], max_docs: int = 12, max_chars: int = 1800) -> str:
    if not docs:
        return "未从知识库检索到可用证据。此时 evidence 数组必须为空，不要编造文献来源。"

    blocks = []
    for index, doc in enumerate(docs[:max_docs], start=1):
        metadata = doc.metadata or {}
        title = metadata.get("title") or metadata.get("doc_name") or metadata.get("source") or metadata.get("source_file") or "未知来源"
        section = metadata.get("section") or metadata.get("chapter") or ""
        page = metadata.get("page", metadata.get("page_number", ""))
        source = metadata.get("doc_path") or metadata.get("source") or metadata.get("source_file") or ""
        citation = f"{title} | {section or '未标注章节'}"
        if page not in ("", None, -1):
            citation = f"{citation} | 页码: {page}"
        text = _squash(doc.page_content)[:max_chars]
        blocks.append(
            "\n".join(
                [
                    f"[证据{index}]",
                    f"引用ID: 证据{index}",
                    f"可填入source: {citation}",
                    f"来源: {title}",
                    f"章节: {section}" if section else "章节: 未标注",
                    f"页码: {page}" if page not in ("", None, -1) else "页码: 未标注",
                    f"文件: {source}" if source else "文件: 未标注",
                    f"内容: {text}",
                ]
            )
        )
    return "\n\n".join(blocks)


def build_judge_prompt(case: MNGSCase, evidence: EvidenceBundle) -> str:
    case_summary = "\n".join(
        [
            f"病原类型: {case.pathogen_type or '未提供'}",
            f"种: {case.species_latin or '未提供'} / {case.species_chinese or '未提供'}",
            f"属: {case.genus_latin or '未提供'} / {case.genus_chinese or '未提供'}",
            f"样本/检测组织: {case.sample_type or '未提供'}",
            f"患者: 年龄={case.age or '未提供'}, 性别={case.sex or '未提供'}, 免疫状态={case.immune_status or '未提供'}",
            f"临床表型: {case.phenotype or '未提供'}",
            f"医生诊断: {case.diagnosis or '未提供'}",
            (
                "mNGS指标: "
                f"种reads={case.reads or '未提供'}, 属reads={case.genus_reads or '未提供'}, "
                f"覆盖率={case.coverage or '未提供'}, 种丰度={case.abundance or '未提供'}, "
                f"属丰度={case.genus_abundance or '未提供'}, 属排名={case.genus_rank or '未提供'}, "
                f"种排名={case.species_rank or '未提供'}"
            ),
            f"输入中的致病性资料: {case.pathogenicity_text or '未提供'}",
        ]
    )

    query_text = "\n".join(f"- {query}" for query in evidence.queries)
    evidence_rule = (
        "【知识库证据】中已经检索到真实证据，evidence 数组必须至少包含 1 条，"
        "source 必须填写对应证据块里的“可填入source”，不要只写证据编号。"
        if evidence.docs
        else "【知识库证据】为空，evidence 必须输出空数组 []，不要编造来源。"
    )
    return f"""你是临床感染病学和 mNGS 结果解读专家。请先基于知识库证据，再结合患者情况和 mNGS 检出可信度，判断单个检出病原是否为当前患者的有害病原。

判断原则：
1. 区分“病原本身可致病”和“当前患者当前样本中是否有害”。
2. 优先考虑样本部位、临床表型、免疫状态、检出序列数、覆盖率、丰度、属/种排名。
3. 证据分层：直接同种同部位证据 > 同种其他部位证据 > 同属/同类类比证据 > 背景知识。
4. 如果检出信号弱、临床表型/样本部位不匹配、且缺乏直接证据，应倾向“无害”。
5. 病例输入中的 reads、覆盖率、丰度、样本部位、免疫状态等只能作为病例分析依据写入 explanation，不能作为 evidence.source。
6. evidence 数组只能引用【知识库证据】中真实出现的证据；如果没有可用知识库证据，evidence 必须输出空数组 []，不要编造来源。
7. 如果输出用于严格评测，只取 label 字段即可；但本次需要同时给出解释和引用。
8. {evidence_rule}

【结构化病例信息】
{case_summary}

【知识库检索 Query】
{query_text}

【知识库证据】
{evidence.context}

【原始输入】
{case.raw_question}

请输出严格 JSON，不要 Markdown，不要代码块：
{{
  "label": "有害或无害",
  "explanation": "用中文解释判断原因，说明 mNGS 可信度、样本部位、临床表型、免疫状态和病原致病性如何共同影响结论。",
  "evidence": [
    {{
      "source": "填写对应证据块中的“可填入source”，必须包含真实文档名和章节；不要只写证据1，也不要填写 mNGS检出指标分析 等自造来源",
      "summary": "该知识库证据如何影响判断；病例输入自身的 mNGS 指标不要写在 evidence 中"
    }}
  ],
  "limitations": "说明缺少哪些关键证据或需要临床补充验证的信息"
}}"""


def _extract_dict_after(text: str, marker: str) -> dict:
    pos = text.find(marker)
    if pos < 0:
        return {}
    start = text.find("{", pos)
    if start < 0:
        return {}

    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw = text[start : idx + 1]
                try:
                    value = ast.literal_eval(raw)
                except Exception:
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def _parse_loose_json_object(text: str) -> dict:
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith('"messages"') or stripped.startswith("'messages'"):
        candidates.append("{" + stripped + "}")
    if stripped.startswith("{") and '"role"' in stripped and '"content"' in stripped:
        candidates.append("[" + stripped + "]")
    if '\\"role\\"' in stripped and '\\"content\\"' in stripped:
        unescaped = stripped.replace('\\"', '"')
        candidates.append(unescaped)
        if unescaped.startswith("{"):
            candidates.append("[" + unescaped + "]")
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            value = None
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            messages = [item for item in value if isinstance(item, dict)]
            if messages:
                return {"messages": messages}
        try:
            value = ast.literal_eval(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            messages = [item for item in value if isinstance(item, dict)]
            if messages:
                return {"messages": messages}
    return {}


def _expand_messages_text(question: str, payload: dict) -> str:
    pieces = [question]
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                pieces.append(content)
    if not isinstance(messages, list):
        pieces.extend(_extract_json_content_strings(question))
        if '\\"content\\"' in question:
            pieces.extend(_extract_json_content_strings(question.replace('\\"', '"')))
    return "\n".join(pieces)


def _extract_json_content_strings(text: str) -> list[str]:
    contents: list[str] = []
    pattern = r'"content"\s*:\s*"((?:\\.|[^"\\])*)"'
    for match in re.finditer(pattern, text, flags=re.S):
        raw = match.group(1)
        try:
            value = json.loads(f'"{raw}"')
        except Exception:
            value = raw.replace("\\n", "\n").replace("\\t", "\t")
        if value:
            contents.append(value)
    return contents


def _normalize_immune_status(value) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    mapping = {
        "0": "未知",
        "1": "正常",
        "2": "抑制",
        "3": "低下",
    }
    return mapping.get(text, text)


def _quoted_field(text: str, key: str) -> str:
    escaped_key = re.escape(key)
    patterns = (
        rf'"{escaped_key}"\s*:\s*"([^"]*)"',
        rf"'{escaped_key}'\s*:\s*'([^']*)'",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _dict_like_field(text: str, key: str) -> str:
    escaped_key = re.escape(key)
    patterns = (
        rf"'{escaped_key}'\s*:\s*'([^']*)'",
        rf'"{escaped_key}"\s*:\s*"([^"]*)"',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _search_value(text: str, pattern: str, flags: int = 0) -> str:
    match = re.search(pattern, text, flags)
    if not match:
        return ""
    return _squash(match.group(1)).strip("。；;，, ")


def _str(value) -> str:
    return "" if value is None else str(value).strip()


def _clean_taxon(value: str) -> str:
    return value.strip().strip("'\"")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unique(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = _squash(item)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _doc_key(doc: Document) -> str:
    metadata = doc.metadata or {}
    source = metadata.get("doc_path") or metadata.get("source") or metadata.get("source_file") or ""
    chunk_id = metadata.get("chunk_id") or metadata.get("pk") or ""
    page = metadata.get("page", "")
    return f"{source}|{chunk_id}|{page}|{doc.page_content[:80]}"
