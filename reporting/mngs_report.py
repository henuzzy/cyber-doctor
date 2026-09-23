"""Adapters from the project's mNGS judgement JSON to PDF report cases."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import json
import re

from .explainability_pdf import build_explainability_pdf


def _evidence_lines(value: Any) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    lines: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            source = str(item.get("source") or "").strip()
            support_type = str(item.get("support_type") or "").strip()
            summary = str(item.get("summary") or "").strip()
            prefix = " | ".join(part for part in (source, support_type) if part)
            line = f"{prefix}：{summary}" if prefix and summary else prefix or summary
        else:
            line = str(item).strip()
        if line:
            lines.append(line)
    return lines


def case_from_judgement(judgement: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the current mNGS JSON contract for the PDF renderer."""
    references = judgement.get("references") or judgement.get("致病性参考")
    if not references:
        references = judgement.get("evidence") or judgement.get("证据")
    return {
        "pathogen": judgement.get("Chinese") or judgement.get("species_chinese") or judgement.get("Latin") or "未提供",
        "pathogen_latin": judgement.get("Latin") or judgement.get("species_latin") or "",
        "case_id": judgement.get("UUID") or judgement.get("case_id") or "",
        "label": judgement.get("label") or judgement.get("Label") or "",
        "confidence": judgement.get("confidence") or judgement.get("解释置信度") or "未提供",
        "patient_summary": judgement.get("patient_summary") or judgement.get("患者摘要") or "",
        "mngs_evidence": judgement.get("mngs_evidence") or judgement.get("mNGS证据") or [],
        "clinical_match": judgement.get("clinical_match") or judgement.get("临床证据匹配") or "",
        "references": _evidence_lines(references),
        "explanation": judgement.get("explanation") or judgement.get("解释") or "",
        "limitations": judgement.get("limitations") or judgement.get("证据局限") or [],
        "review_items": judgement.get("review_items") or judgement.get("建议复核项") or [],
    }


def export_judgement_pdf(output_path: str, judgement: Mapping[str, Any]) -> str:
    return export_judgements_pdf(output_path, [judgement])


def export_judgements_pdf(output_path: str, judgements: Sequence[Mapping[str, Any]]) -> str:
    cases = [case_from_judgement(item) for item in judgements]
    labels = {str(case.get("label") or "") for case in cases}
    label_prefix = next(iter(labels)) if len(labels) == 1 and next(iter(labels)) in {"有害", "无害"} else ""
    title = f"{_count_text(len(cases))}个{label_prefix}病原可解释性诊断报告"
    return build_explainability_pdf(
        output_path,
        cases=cases,
        title=title,
        subtitle="基于结构化 PubMed / UpToDate 文档与 Qwen 分析",
        scope=(
            "本报告逐例匹配病原相关的结构化 PubMed 和 UpToDate 文档，将文档证据与病例信息一并交由模型分析，"
            "并汇总输入中的全部病原。已有结论仅作解释，不因背景文献自动改判。"
        ),
        compact=len(cases) > 1,
    )


def _count_text(count: int) -> str:
    chinese = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    return chinese.get(count, str(count))


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for start, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    aggregates = [item for item in candidates if isinstance(item.get("cases") or item.get("病例"), list)]
    return aggregates[-1] if aggregates else (candidates[-1] if candidates else {})


def _judgement_items(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = value.get("cases") or value.get("病例")
    if isinstance(items, list):
        return [dict(item) for item in items if isinstance(item, Mapping)]
    return [dict(value)]


def _apply_case_defaults(judgement: dict[str, Any], case: Any) -> None:
    judgement.setdefault("Latin", case.species_latin)
    judgement.setdefault("Chinese", case.species_chinese)
    judgement.setdefault("UUID", case.case_id)
    judgement.setdefault("label", case.existing_label)
    judgement.setdefault("patient_summary", "、".join(filter(None, [case.age, case.sex, case.diagnosis, case.phenotype])))
    judgement.setdefault("mngs_evidence", [
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
    ])
    judgement.setdefault("references", [case.pathogenicity_text] if case.pathogenicity_text else [])
    judgement.setdefault("confidence", "未提供")


def export_latest_chat_pdf(output_path: str, history: list[list[Any]] | None) -> str:
    """Export the latest mNGS judgement in a Gradio chatbot history."""
    if not history:
        raise ValueError("当前没有可导出的对话结果")
    latest = next((pair for pair in reversed(history) if pair and len(pair) >= 2), None)
    if not latest:
        raise ValueError("当前没有可导出的对话结果")
    user_text, assistant_text = str(latest[0] or ""), str(latest[1] or "")
    payload = _parse_json_response(assistant_text)
    if not payload:
        raise ValueError("当前回答不是可导出的结构化 mNGS 判别结果")

    judgements = _judgement_items(payload)
    if not judgements:
        raise ValueError("当前回答没有包含任何可导出的病例结果")

    # New multi-case results are self-contained. The fallback below preserves
    # compatibility with historical single-case chat answers.
    if len(judgements) == 1 and not (judgements[0].get("Latin") or judgements[0].get("Chinese")):
        from mngs.rag_judge import parse_mngs_case

        _apply_case_defaults(judgements[0], parse_mngs_case(user_text))

    missing_names = [index for index, item in enumerate(judgements, start=1) if not (item.get("Latin") or item.get("Chinese"))]
    if missing_names:
        raise ValueError(f"第 {'、'.join(map(str, missing_names))} 个模型结果缺少病原名称，已停止生成 PDF")
    return export_judgements_pdf(output_path, judgements)
