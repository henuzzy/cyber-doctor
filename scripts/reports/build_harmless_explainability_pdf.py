#!/usr/bin/env python3
"""Build and visually stable A4 explainability report PDF."""
from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

REPORT_DIR = Path(r"E:\华大医疗agent资料\前20个病原的文章-uptodate-pubmed\结构化文档\报告对比_Qwen3.8-27b_基于GPT-5.6-Terra结构化文档")
CONTENT = REPORT_DIR / "三个无害病原_解释内容_Qwen3.8-27b.json"
OUTPUT = REPORT_DIR / "三个无害病原_可解释性诊断报告_Qwen3.8-27b_基于GPT-5.6-Terra结构化文档.pdf"

NAVY = colors.HexColor("#18243A")
BLUE = colors.HexColor("#2F6FED")
PALE_BLUE = colors.HexColor("#EAF2FF")
LIGHT = colors.HexColor("#F6F8FB")
LINE = colors.HexColor("#D8E0EA")
MUTED = colors.HexColor("#526174")
GREEN = colors.HexColor("#18A66A")
AMBER = colors.HexColor("#E6A817")
WHITE = colors.white


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("YaHei", r"C:\Windows\Fonts\msyh.ttc"))
    pdfmetrics.registerFont(TTFont("Hei", r"C:\Windows\Fonts\simhei.ttf"))


def esc(text: object) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def confidence_color(value: str):
    return {"高": GREEN, "中": AMBER, "低": colors.HexColor("#8793A1")}.get(value, BLUE)


def page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(22 * mm, 16 * mm, 188 * mm, 16 * mm)
    canvas.setFont("YaHei", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(22 * mm, 10 * mm, "mNGS 可解释性诊断报告 | 仅供临床综合研判")
    canvas.drawRightString(188 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def section_bar(title: str, styles) -> Table:
    table = Table([[Paragraph(esc(title), styles["section"]) ]], colWidths=[166 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
        ("BOX", (0, 0), (-1, -1), 0, PALE_BLUE),
        ("LINEBEFORE", (0, 0), (0, 0), 4, BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def card(text: str, styles, accent=BLUE) -> Table:
    table = Table([[Paragraph(esc(text), styles["body"]) ]], colWidths=[166 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("LINEBEFORE", (0, 0), (0, 0), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def bullets(items: list[str], styles) -> list:
    story = []
    for item in items:
        story.append(Paragraph(f"<font color='#2F6FED'>■</font>&nbsp;&nbsp;{esc(item)}", styles["bullet"]))
        story.append(Spacer(1, 2.2 * mm))
    return story


def validate_content(data: dict) -> None:
    cases = data.get("病例", [])
    expected_ids = {"23B2024080804", "23B2023122304", "23B2023091904"}
    if len(cases) != 3 or {case.get("UUID") for case in cases} != expected_ids:
        raise ValueError("Report content must contain exactly the three requested cases")
    if any(case.get("既有结论") != "无害" for case in cases):
        raise ValueError("Report content changed an existing harmless label")
def main() -> None:
    register_fonts()
    data = json.loads(CONTENT.read_text(encoding="utf-8"))
    validate_content(data)
    cases = data["病例"]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Hei", fontSize=22, leading=30, textColor=NAVY, alignment=TA_CENTER, spaceAfter=6 * mm),
        "subtitle": ParagraphStyle("subtitle", fontName="YaHei", fontSize=9.5, leading=15, textColor=MUTED, alignment=TA_CENTER),
        "section": ParagraphStyle("section", fontName="Hei", fontSize=13, leading=18, textColor=NAVY),
        "case_title": ParagraphStyle("case_title", fontName="Hei", fontSize=16, leading=22, textColor=NAVY),
        "label": ParagraphStyle("label", fontName="Hei", fontSize=10, leading=15, textColor=NAVY),
        "body": ParagraphStyle("body", fontName="YaHei", fontSize=9, leading=16, textColor=MUTED, alignment=TA_LEFT),
        "bullet": ParagraphStyle("bullet", fontName="YaHei", fontSize=8.7, leading=15, textColor=MUTED, leftIndent=1 * mm),
        "small": ParagraphStyle("small", fontName="YaHei", fontSize=7.8, leading=13, textColor=MUTED),
        "meta": ParagraphStyle("meta", fontName="YaHei", fontSize=9, leading=15, textColor=NAVY),
    }

    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm, topMargin=19 * mm, bottomMargin=22 * mm,
        title="三个无害病原可解释性诊断报告（Qwen3.8-27b）",
        author="DGX qwen3.8-27b; structured evidence from GPT-5.6-Terra",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=page_footer)])
    story = []

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(esc(data.get("报告标题", "可解释性诊断报告")), styles["title"]))
    story.append(Paragraph("Qwen3.8-27b 原始输出 | 基于 GPT-5.6-Terra 结构化文档", styles["subtitle"]))
    story.append(Spacer(1, 7 * mm))
    story.append(section_bar("总体说明", styles))
    story.append(Spacer(1, 4 * mm))
    story.append(card(
        data.get("总体说明", ""),
        styles, BLUE,
    ))
    story.append(Spacer(1, 7 * mm))

    summary_rows = [[Paragraph("病原", styles["label"]), Paragraph("病例", styles["label"]), Paragraph("既有结论", styles["label"]), Paragraph("解释置信度", styles["label"])]]
    for case in cases:
        c = confidence_color(case["解释置信度"])
        summary_rows.append([
            Paragraph(f"{esc(case['病原中文名'])}<br/><font size='7'>{esc(case['病原拉丁名'])}</font>", styles["meta"]),
            Paragraph(esc(case["UUID"]), styles["small"]),
            Paragraph(f"<font color='{GREEN.hexval()}'>●</font> 无害", styles["meta"]),
            Paragraph(f"<font color='{c.hexval()}'>●</font> {esc(case['解释置信度'])}", styles["meta"]),
        ])
    table = Table(summary_rows, colWidths=[63 * mm, 39 * mm, 30 * mm, 34 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(table)
    story.append(PageBreak())

    for index, case in enumerate(cases, 1):
        c = confidence_color(case["解释置信度"])
        title_row = Table([
            [Paragraph(f"{index:02d}  {esc(case['病原中文名'])}", styles["case_title"]),
             Paragraph(f"<font color='{GREEN.hexval()}'>●</font> 无害&nbsp;&nbsp; <font color='{c.hexval()}'>●</font> {esc(case['解释置信度'])}置信度", styles["label"])]
        ], colWidths=[100 * mm, 66 * mm])
        title_row.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
            ("LINEBEFORE", (0, 0), (0, 0), 4, BLUE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.append(title_row)
        story.append(Spacer(1, 3 * mm))
        meta_rows = [
            ["拉丁名", case["病原拉丁名"], "病例编号", case["UUID"]],
            ["既有标签", "无害", "解释置信度", case["解释置信度"]],
            ["患者摘要", case["患者摘要"], "", ""],
        ]
        meta_table = Table([[Paragraph(esc(x), styles["meta"] if i % 2 else styles["label"]) for i, x in enumerate(row)] for row in meta_rows], colWidths=[25 * mm, 62 * mm, 25 * mm, 54 * mm])
        meta_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
            ("BACKGROUND", (2, 0), (2, 1), PALE_BLUE),
            ("SPAN", (1, 2), (3, 2)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("AI 可解释推演结论", styles["label"]))
        story.append(Spacer(1, 2 * mm))
        story.append(card(case["无害结论解释"], styles, GREEN))
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("mNGS 检出证据", styles["label"]))
        story.append(Spacer(1, 2 * mm))
        story.extend(bullets(case["mNGS证据"], styles))
        story.append(Paragraph("临床证据匹配", styles["label"]))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(esc(case["临床证据匹配"]), styles["body"]))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("致病性资料与适用范围", styles["label"]))
        story.append(Spacer(1, 2 * mm))
        story.extend(bullets(case["致病性参考"], styles))
        story.append(Paragraph("证据局限", styles["label"]))
        story.append(Spacer(1, 2 * mm))
        story.extend(bullets(case["证据局限"], styles))
        story.append(Paragraph("建议复核项", styles["label"]))
        story.append(Spacer(1, 2 * mm))
        story.extend(bullets(case["建议复核项"], styles))
        story.append(Spacer(1, 2 * mm))
        if index < len(cases):
            story.append(PageBreak())

    doc.build(story)
    print(OUTPUT.resolve())


if __name__ == "__main__":
    main()
