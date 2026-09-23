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
    name_id: str = ""
    case_id: str = ""
    existing_label: str = ""
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


def build_mngs_rag_prompt(question: str | MNGSCase) -> tuple[str, EvidenceBundle]:
    case = question if isinstance(question, MNGSCase) else parse_mngs_case(question)
    evidence = retrieve_mngs_evidence(case)
    prompt = build_judge_prompt(case, evidence)
    return prompt, evidence


def judge_with_rag_stream(question: str, history: List[List | None] | None = None):
    """Analyze every case independently, then yield one aggregate JSON result.

    The case prompt already contains the complete patient record and only the
    structured article records matched to that pathogen. Chat history is
    intentionally excluded so a previous patient's facts cannot leak into a
    new report.
    """
    from client.clientfactory import Clientfactory

    cases = parse_mngs_cases(question)
    client = Clientfactory().get_client()

    def generate():
        results: list[dict] = []
        yield f"已识别 {len(cases)} 个病例，开始逐例匹配结构化文档并分析。\n"
        for index, case in enumerate(cases, start=1):
            prompt, evidence = build_mngs_rag_prompt(case)
            pathogen = case.species_chinese or case.species_latin or case.name_id or f"病例 {index}"
            yield f"\n[{index}/{len(cases)}] {pathogen}：匹配到 {len(evidence.docs)} 篇结构化文章，正在请求模型。\n"
            raw_result = client.chat_with_ai(prompt)
            result = _parse_model_judgement(raw_result)
            results.append(_complete_case_result(case, result, evidence))
            yield f"[{index}/{len(cases)}] {pathogen}：分析完成。\n"

        yield "\n" + json.dumps({"cases": results}, ensure_ascii=False, indent=2)

    return generate()


def parse_mngs_case(question: str) -> MNGSCase:
    case = MNGSCase(raw_question=question)
    payload = _parse_loose_json_object(question)
    working_text = _expand_messages_text(question, payload)

    case.species_latin = _clean_taxon(_str(payload.get("Latin") or _quoted_field(question, "Latin")))
    case.species_chinese = _str(payload.get("Chinese") or _quoted_field(question, "Chinese"))
    case.name_id = _str(payload.get("NameID") or _quoted_field(question, "NameID"))
    case.case_id = _str(payload.get("UUID") or payload.get("case_id") or _quoted_field(question, "UUID"))
    case.existing_label = _extract_existing_label(payload)
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
    if not case.species_latin and not case.species_chinese:
        try:
            from model.RAG.structured_retriever import find_pathogen
            record = find_pathogen(question)
        except Exception:
            record = None
        if record:
            names = record.get("病原名称") or {}
            case.name_id = case.name_id or _str(record.get("NameID"))
            case.species_latin = _str(names.get("种-拉丁名"))
            case.species_chinese = _str(names.get("种-中文名"))
            case.genus_latin = _str(names.get("属-拉丁名"))
            case.genus_chinese = _str(names.get("属-中文名"))
            case.pathogen_type = case.pathogen_type or _str(record.get("病原类型"))

    return case


def parse_mngs_cases(question: str) -> list[MNGSCase]:
    """Parse JSON, JSON arrays, or JSONL containing one or more independent cases."""
    text = str(question or "").strip()
    cleaned = re.sub(r"```(?:json|jsonl)?\s*|```", "", text, flags=re.I).strip()
    payloads = _case_payloads(cleaned)
    if not payloads:
        if len(re.findall(r"病原基本信息", text)) > 1:
            raise ValueError("检测到多条病原信息，但无法识别病例边界；请使用 JSON 数组或每行一条 JSON 的 JSONL 格式。")
        return [parse_mngs_case(text)]

    cases = [parse_mngs_case(json.dumps(payload, ensure_ascii=False)) for payload in payloads]
    if len(cases) > 1:
        missing = [index for index, case in enumerate(cases, start=1) if not (case.species_latin or case.species_chinese or case.name_id)]
        if missing:
            joined = "、".join(map(str, missing))
            raise ValueError(f"第 {joined} 条病例记录未能解析到病原名称或 NameID；已停止以免漏病例生成报告。")
    return cases


def _case_payloads(text: str) -> list[dict]:
    if not text:
        return []

    values: list[object] = []
    try:
        values = [json.loads(text)]
    except json.JSONDecodeError:
        # raw_decode also handles pretty-printed JSON objects concatenated
        # together, in addition to ordinary one-record-per-line JSONL.
        decoder = json.JSONDecoder()
        position = 0
        decoded: list[object] = []
        while position < len(text):
            while position < len(text) and (text[position].isspace() or text[position] == ","):
                position += 1
            if position >= len(text):
                break
            try:
                value, end = decoder.raw_decode(text, position)
            except json.JSONDecodeError:
                decoded = []
                break
            decoded.append(value)
            position = end
        values = decoded
        if not values:
            # Uploaded Markdown is prefixed by the UI with "文本N内容：".
            # Parse one complete outer JSON object from each non-empty line so
            # that this display prefix cannot collapse JSONL into one case.
            values = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                start = line.find("{")
                if start < 0:
                    continue
                try:
                    value, _ = decoder.raw_decode(line[start:])
                except json.JSONDecodeError:
                    continue
                values.append(value)

    records: list[dict] = []
    for value in values:
        if isinstance(value, dict):
            nested = next((value.get(key) for key in ("cases", "病例", "records", "items") if isinstance(value.get(key), list)), None)
            if nested is not None:
                records.extend(item for item in nested if isinstance(item, dict))
            elif isinstance(value.get("messages"), list) or any(
                key in value for key in ("UUID", "Latin", "Chinese", "NameID", "病原基本信息")
            ):
                records.append(value)
        elif isinstance(value, list):
            if value and all(isinstance(item, dict) and "role" in item and "content" in item for item in value):
                records.append({"messages": value})
            else:
                records.extend(item for item in value if isinstance(item, dict))

    return records


def _extract_existing_label(payload: dict) -> str:
    direct = _str(payload.get("Label") or payload.get("label") or payload.get("OriginalFlag"))
    if direct in {"有害", "无害"}:
        return direct
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "assistant":
                content = str(message.get("content") or "").strip()
                if content in {"有害", "无害"}:
                    return content
                break
    return ""


def _parse_model_judgement(value: object) -> dict:
    text = str(value or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    decoder = json.JSONDecoder()
    for position, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            result, _ = decoder.raw_decode(cleaned[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    raise ValueError("模型没有返回可解析的 JSON 病例结果，已停止生成 PDF 以免遗漏或错配病例。")


def _complete_case_result(case: MNGSCase, result: dict, evidence: EvidenceBundle) -> dict:
    model_label = _str(result.get("label") or result.get("Label"))
    if case.existing_label:
        label = case.existing_label
    elif model_label in {"有害", "无害"}:
        label = model_label
    else:
        raise ValueError(f"{case.species_chinese or case.species_latin or '病例'} 的模型结果缺少有效 label。")

    references = result.get("evidence") or result.get("references") or result.get("致病性参考") or []
    if references and not evidence.docs:
        # A no-hit query must never acquire fabricated article citations.
        references = []

    return {
        "UUID": case.case_id,
        "NameID": case.name_id,
        "Latin": case.species_latin,
        "Chinese": case.species_chinese,
        "label": label,
        "confidence": result.get("confidence") or result.get("解释置信度") or "未提供",
        "patient_summary": result.get("patient_summary") or result.get("患者摘要") or _patient_summary(case),
        "mngs_evidence": result.get("mngs_evidence") or result.get("mNGS证据") or _mngs_evidence(case),
        "clinical_match": result.get("clinical_match") or result.get("临床证据匹配") or "未提供",
        "evidence": references,
        "explanation": result.get("explanation") or result.get("解释") or result.get("无害结论解释") or "未提供",
        "limitations": result.get("limitations") or result.get("证据局限") or [],
        "review_items": result.get("review_items") or result.get("建议复核项") or [],
        "matched_document_count": len(evidence.docs),
    }


def _patient_summary(case: MNGSCase) -> str:
    parts = [
        f"{case.age}岁" if case.age else "",
        case.sex,
        case.diagnosis,
        f"免疫状态：{case.immune_status}" if case.immune_status else "",
        f"临床表型：{case.phenotype}" if case.phenotype else "",
    ]
    return "，".join(part for part in parts if part)


def _mngs_evidence(case: MNGSCase) -> list[str]:
    return [
        item for item in [
            f"检测组织：{case.sample_type}" if case.sample_type else "",
            f"种检出序列数：{case.reads}" if case.reads else "",
            f"属检出序列数：{case.genus_reads}" if case.genus_reads else "",
            f"覆盖率：{case.coverage}" if case.coverage else "",
            f"种丰度：{case.abundance}" if case.abundance else "",
            f"属丰度：{case.genus_abundance}" if case.genus_abundance else "",
            f"属排名：{case.genus_rank}" if case.genus_rank else "",
            f"种排名：{case.species_rank}" if case.species_rank else "",
        ] if item
    ]


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
        from model.RAG.structured_retriever import retrieve
    except Exception as exc:
        print(f"structured pathogen retriever is unavailable: {exc}")
        return EvidenceBundle(queries=queries, docs=[], context=format_evidence_docs([]))

    species_names = [
        case.name_id,
        case.species_chinese,
        case.species_latin,
        case.species_latin.replace("_", " ") if case.species_latin else "",
    ]
    try:
        docs = retrieve(species_names, top_k=12)
        for doc in docs:
            record = doc.metadata.get("structured_record") or {}
            names = record.get("病原名称") or {}
            target_species = _normalize_name(case.species_latin)
            article_species = _normalize_name(names.get("种-拉丁名") or names.get("种-中文名"))
            if target_species and article_species == target_species:
                scope = "目标物种直接匹配"
            elif case.name_id and str(record.get("NameID") or "") == case.name_id:
                scope = "病例关联文件夹中的同类或相关病原参考"
            else:
                scope = "同属或相关病原群参考"
            doc.metadata["support_scope"] = scope
        if not docs:
            related_names = [case.genus_chinese, case.genus_latin]
            related_names.extend(re.findall(r"\b([A-Z][a-z]+)\s+[a-z][A-Za-z-]+\b", case.pathogenicity_text))
            docs = retrieve(_unique(related_names), top_k=12)
            for doc in docs:
                doc.metadata.setdefault("support_scope", "同属或相关病原群参考")
    except Exception as exc:
        print(f"structured pathogen retrieval failed: {exc}")
        docs = []
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
        source_type = metadata.get("source") or "来源未标注"
        support_scope = metadata.get("support_scope") or "关联范围未标注"
        citation = f"{title} | {source_type} | {section or '未标注章节'}"
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
                    f"来源类型: {source_type}",
                    f"与目标病原关系: {support_scope}",
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
            f"病例编号: {case.case_id or '未提供'}",
            f"输入已有结论: {case.existing_label or '未提供'}",
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
    task_rule = (
        f"本病例已有结论为“{case.existing_label}”。不得重新分类或修改该结论；任务是结合病例和知识库解释这个既有结论。"
        if case.existing_label
        else "本病例没有提供既有结论；请结合病例和知识库判断有害或无害，并解释判断。"
    )
    return f"""你是临床感染病学和 mNGS 可解释性报告专家。请基于按病原名称匹配到的结构化 PubMed 和 UpToDate 文章，并结合当前病例信息，生成单病例的结构化解释结果。

任务边界：{task_rule}

判断原则：
1. 区分“病原本身可致病”和“当前患者当前样本中是否有害”。
2. 优先考虑样本部位、临床表型、免疫状态、检出序列数、覆盖率、丰度、属/种排名。
3. 证据分层：直接同种同部位证据 > 同种其他部位证据 > 同属/同类类比证据 > 背景知识。
4. 如果检出信号弱、临床表型/样本部位不匹配、且缺乏直接证据，应倾向“无害”。
5. 病例输入中的 reads、覆盖率、丰度、样本部位、免疫状态等只能作为病例分析依据写入 explanation，不能作为 evidence.source。
6. evidence 数组只能引用【知识库证据】中真实出现的证据；如果没有可用知识库证据，evidence 必须输出空数组 []，不要编造来源。
7. label 必须遵守任务边界；如果病例已有结论，输出相同 label，不得用知识库背景资料推翻它。
8. {evidence_rule}
9. 结构化证据中的 PubMed 和 UpToDate 字段结构不同，必须按来源分别理解，不能混用字段或把 UpToDate 的群体背景改写为目标物种事实。
10. 引用 source 时使用证据块中真实出现的来源标题、来源类型和来源文件，不要编造文章。
11. 每项知识库证据必须说明它是目标物种直接证据，还是同属/相关病原群参考；后者不得外推为目标物种事实。
12. 所有字段只使用输入病例和知识库明确写出的内容；没有明确内容就写空字符串或空数组。

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
  "confidence": "高/中/低",
  "patient_summary": "患者年龄、性别、诊断、免疫状态和临床表型的简要汇总",
  "mngs_evidence": ["逐项陈述输入中的检测组织、reads、覆盖率、丰度和排名，不添加阈值"],
  "clinical_match": "说明病例表型、样本部位与文献证据的匹配程度，不把相关性写成因果",
  "explanation": "解释既有结论或判断结果，明确区分病例证据和病原背景资料",
  "evidence": [
    {{
      "source": "填写对应证据块中的“可填入source”，必须包含真实文档名和章节；不要只写证据1，也不要填写 mNGS检出指标分析 等自造来源",
      "support_type": "目标物种直接匹配/同属或相关病原群参考",
      "summary": "该知识库证据如何影响判断；病例输入自身的 mNGS 指标不要写在 evidence 中"
    }}
  ],
  "limitations": ["说明缺少哪些关键证据或需要临床补充验证的信息"],
  "review_items": ["仅列条件性的复核方向，不提供输入和知识库之外的治疗结论"]
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


def _normalize_name(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


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
