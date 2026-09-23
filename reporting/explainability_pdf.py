"""Generate a compact A4 explainability report for an mNGS judgement.

The renderer is deliberately independent from the LLM and RAG layers.  Callers
provide the already-produced judgement and evidence, so exporting a report does
not trigger a second model request or change the clinical decision.
"""
from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#18243A")
BLUE = colors.HexColor("#2F6FED")
PALE_BLUE = colors.HexColor("#EAF2FF")
LIGHT = colors.HexColor("#F6F8FB")
LINE = colors.HexColor("#D8E0EA")
MUTED = colors.HexColor("#2F3B4B")
GREEN = colors.HexColor("#18A66A")
AMBER = colors.HexColor("#E6A817")
_PDF_REGULAR_FONT = "CyberDoctorPDFRegular"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _font_path(candidates: Sequence[str]) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and path.is_file():
            return str(path)
    return None


def _register_ttf(name: str, candidates: Sequence[str], *, subfont_index: int = 0) -> str | None:
    for candidate in candidates:
        path = _font_path([candidate])
        if not path:
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=subfont_index))
            return name
        except (TTFError, OSError, ValueError) as exc:
            print(f"跳过不可嵌入的 PDF 字体 {path}: {exc}")
    return None


def _register_fonts() -> tuple[str, str]:
    global _PDF_REGULAR_FONT
    project_fonts = [
        Path(__file__).resolve().parents[1] / "fonts" / "wqy-microhei.ttc",
        Path(__file__).resolve().parents[2] / "fonts" / "wqy-microhei.ttc",
    ]
    regular_candidates = [
        os.getenv("CYBER_DOCTOR_PDF_FONT", ""),
        *(str(path) for path in project_fonts),
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    bold_candidates = [
        os.getenv("CYBER_DOCTOR_PDF_BOLD_FONT", ""),
        r"C:\Windows\Fonts\msyhbd.ttc",
    ]
    regular = "CyberDoctorPDFRegular" if "CyberDoctorPDFRegular" in pdfmetrics.getRegisteredFontNames() else _register_ttf("CyberDoctorPDFRegular", regular_candidates)
    bold = "CyberDoctorPDFBold" if "CyberDoctorPDFBold" in pdfmetrics.getRegisteredFontNames() else _register_ttf("CyberDoctorPDFBold", bold_candidates)
    if not regular:
        raise RuntimeError(
            "No embeddable CJK TrueType font found for PDF generation. "
            "Install Droid Sans Fallback or set CYBER_DOCTOR_PDF_FONT."
        )
    if not bold:
        bold = regular
    _PDF_REGULAR_FONT = regular
    return regular, bold


def _confidence_color(value: str):
    return {"高": GREEN, "中": AMBER, "低": colors.HexColor("#8793A1")}.get(value, BLUE)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(22 * mm, 16 * mm, 188 * mm, 16 * mm)
    canvas.setFont(_PDF_REGULAR_FONT, 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(22 * mm, 10 * mm, "mNGS 可解释性报告 | 不替代临床诊断或治疗决定")
    canvas.drawRightString(188 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _styles(regular: str, bold: str, *, compact: bool = False) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    if compact:
        title_size, title_leading = 20, 26
        section_size, section_leading = 12, 15
        case_size, case_leading = 15, 19
        label_size, label_leading = 9.5, 13
        body_size, body_leading = 9.4, 14
        bullet_size, bullet_leading = 9.2, 13.5
        small_size, small_leading = 8.5, 12
        meta_size, meta_leading = 9, 13
    else:
        title_size, title_leading = 22, 30
        section_size, section_leading = 13, 18
        case_size, case_leading = 16, 22
        label_size, label_leading = 10.5, 15
        body_size, body_leading = 10.5, 16
        bullet_size, bullet_leading = 10, 15
        small_size, small_leading = 9, 13.5
        meta_size, meta_leading = 10, 15
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName=bold, fontSize=title_size, leading=title_leading, textColor=NAVY, alignment=TA_CENTER, spaceAfter=6 * mm),
        "subtitle": ParagraphStyle("subtitle", fontName=regular, fontSize=10, leading=15 if not compact else 13, textColor=MUTED, alignment=TA_CENTER),
        "section": ParagraphStyle("section", fontName=bold, fontSize=section_size, leading=section_leading, textColor=NAVY),
        "case_title": ParagraphStyle("case_title", fontName=bold, fontSize=case_size, leading=case_leading, textColor=NAVY),
        "label": ParagraphStyle("label", fontName=bold, fontSize=label_size, leading=label_leading, textColor=NAVY),
        "body": ParagraphStyle("body", fontName=regular, fontSize=body_size, leading=body_leading, textColor=MUTED, alignment=TA_LEFT),
        "bullet": ParagraphStyle("bullet", fontName=regular, fontSize=bullet_size, leading=bullet_leading, textColor=MUTED, leftIndent=1 * mm),
        "small": ParagraphStyle("small", fontName=regular, fontSize=small_size, leading=small_leading, textColor=MUTED),
        "meta": ParagraphStyle("meta", fontName=regular, fontSize=meta_size, leading=meta_leading, textColor=NAVY),
    }


def _section_bar(title: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    table = Table([[Paragraph(_escape(title), styles["section"])]], colWidths=[166 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
        ("LINEBEFORE", (0, 0), (0, 0), 4, BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def _card(text: str, styles: Mapping[str, ParagraphStyle], accent=BLUE) -> Table:
    table = Table([[Paragraph(_escape(text), styles["body"])]], colWidths=[166 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("LINEBEFORE", (0, 0), (0, 0), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def _bullets(items: Sequence[str], styles: Mapping[str, ParagraphStyle]) -> list:
    story = []
    for item in items:
        story.extend([
            Paragraph(f"<font color='#2F6FED'>■</font>&nbsp;&nbsp;{_escape(item)}", styles["bullet"]),
            Spacer(1, 2.2 * mm),
        ])
    return story


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def build_explainability_pdf(
    output_path: str | os.PathLike[str],
    *,
    cases: Sequence[Mapping[str, Any]],
    title: str = "可解释性诊断报告",
    subtitle: str = "Explainable mNGS Interpretation Report",
    scope: str = "本报告解释既有 mNGS 判别结果，不重新分类，也不替代培养、靶向PCR或临床诊断。",
    evidence_boundary: Sequence[str] | None = None,
    compact: bool = False,
) -> str:
    """Render cases to an A4 PDF and return the absolute output path.

    Each case accepts the JSON shape emitted by ``mngs.rag_judge`` or a
    compatible mapping: ``pathogen``, ``case_id``, ``label``, ``confidence``,
    ``patient_summary``, ``mngs_evidence``, ``clinical_match``, ``references``,
    ``explanation``, ``limitations`` and ``review_items``.
    """
    if not cases:
        raise ValueError("cases must contain at least one mNGS judgement")
    regular, bold = _register_fonts()
    styles = _styles(regular, bold, compact=compact)
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(destination), pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm, topMargin=19 * mm, bottomMargin=22 * mm,
        title=title, author="cyber-doctor",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=_footer)])
    story = [Spacer(1, 6 * mm), Paragraph(_escape(title), styles["title"]), Paragraph(_escape(subtitle), styles["subtitle"]), Spacer(1, 7 * mm)]
    story.extend([_section_bar("报告范围", styles), Spacer(1, 4 * mm), _card(scope, styles), Spacer(1, 7 * mm)])

    summary_rows = [[Paragraph("病原", styles["label"]), Paragraph("病例", styles["label"]), Paragraph("既有结论", styles["label"]), Paragraph("解释置信度", styles["label"])]]
    for case in cases:
        confidence = str(case.get("confidence") or "未提供")
        color = _confidence_color(confidence)
        summary_rows.append([
            Paragraph(f"{_escape(case.get('pathogen') or case.get('pathogen_chinese') or '未提供')}<br/><font size='8' color='{MUTED.hexval()}'>{_escape(case.get('pathogen_latin') or '')}</font>", styles["meta"]),
            Paragraph(_escape(case.get("case_id") or case.get("uuid") or "未提供"), styles["small"]),
            Paragraph(f"<font color='{GREEN.hexval()}'>●</font> {_escape(case.get('label') or '未提供')}", styles["meta"]),
            Paragraph(f"<font color='{color.hexval()}'>●</font> {_escape(confidence)}", styles["meta"]),
        ])
    summary = Table(summary_rows, colWidths=[63 * mm, 39 * mm, 30 * mm, 34 * mm], repeatRows=1)
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE_BLUE), ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([summary, Spacer(1, 7 * mm), _section_bar("证据层级与边界", styles), Spacer(1, 3 * mm)])
    story.extend(_bullets(evidence_boundary or [
        "病例自身的 mNGS 指标、取样部位、患者表型和免疫状态属于一级证据。",
        "病原可致病的背景资料不能自动证明其在当前患者、当前样本中有害。",
        "缺少培养、靶向PCR或重复检测时，应保留证据局限，不把推测写成结论。",
    ], styles))
    story.append(PageBreak())

    for index, case in enumerate(cases, 1):
        confidence = str(case.get("confidence") or "未提供")
        confidence_color = _confidence_color(confidence)
        title_row = Table([[Paragraph(f"{index:02d}  {_escape(case.get('pathogen') or case.get('pathogen_chinese') or '未命名病原')}", styles["case_title"]), Paragraph(f"<font color='{GREEN.hexval()}'>●</font> {_escape(case.get('label') or '未提供')}&nbsp;&nbsp; <font color='{confidence_color.hexval()}'>●</font> {_escape(confidence)}置信度", styles["label"])]], colWidths=[100 * mm, 66 * mm])
        title_row.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("LINEBEFORE", (0, 0), (0, 0), 4, BLUE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
        story.extend([title_row, Spacer(1, 3 * mm)])
        meta_rows = [["拉丁名", case.get("pathogen_latin", ""), "病例编号", case.get("case_id") or case.get("uuid") or ""], ["既有标签", case.get("label", ""), "解释置信度", confidence], ["患者摘要", case.get("patient_summary", ""), "", ""]]
        meta_table = Table([[Paragraph(_escape(value), styles["meta"] if i % 2 else styles["label"]) for i, value in enumerate(row)] for row in meta_rows], colWidths=[25 * mm, 62 * mm, 25 * mm, 54 * mm])
        meta_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE), ("BACKGROUND", (2, 0), (2, 1), PALE_BLUE), ("SPAN", (1, 2), (3, 2)), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.extend([meta_table, Spacer(1, 5 * mm), Paragraph("AI 可解释推演结论", styles["label"]), Spacer(1, 2 * mm), _card(str(case.get("explanation") or "未提供"), styles, GREEN), Spacer(1, 5 * mm), Paragraph("mNGS 检出证据", styles["label"]), Spacer(1, 2 * mm)])
        story.extend(_bullets(_list(case.get("mngs_evidence")), styles))
        story.extend([Paragraph("临床证据匹配", styles["label"]), Spacer(1, 2 * mm), Paragraph(_escape(case.get("clinical_match") or "未提供"), styles["body"]), Spacer(1, 4 * mm), Paragraph("致病性资料与适用范围", styles["label"]), Spacer(1, 2 * mm)])
        story.extend(_bullets(_list(case.get("references")), styles))
        story.extend([Paragraph("证据局限", styles["label"]), Spacer(1, 2 * mm)])
        story.extend(_bullets(_list(case.get("limitations")), styles))
        story.extend([Paragraph("建议复核项", styles["label"]), Spacer(1, 2 * mm)])
        story.extend(_bullets(_list(case.get("review_items")), styles))
        if index < len(cases):
            story.append(PageBreak())
    doc.build(story)
    return str(destination)
