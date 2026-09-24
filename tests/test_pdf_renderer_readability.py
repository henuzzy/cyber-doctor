from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyPDF2 import PdfReader

from reportlab.lib import colors

from cyber_doctor.reporting import explainability_pdf as renderer


class PdfRendererReadabilityTests(unittest.TestCase):
    def test_compact_body_and_evidence_styles_remain_readable(self) -> None:
        regular, bold = "Regular", "Bold"
        styles = renderer._styles(regular, bold, compact=True)

        self.assertGreaterEqual(styles["body"].fontSize, 9)
        self.assertGreaterEqual(styles["bullet"].fontSize, 9)
        self.assertGreaterEqual(styles["small"].fontSize, 8.5)
        self.assertEqual(colors.HexColor("#2F3B4B"), styles["body"].textColor)

    def test_missing_embeddable_font_fails_instead_of_using_cid_fallback(self) -> None:
        with patch.object(renderer.pdfmetrics, "getRegisteredFontNames", return_value=[]):
            with patch.object(renderer, "_register_ttf", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "No embeddable CJK TrueType font"):
                    renderer._register_fonts()

    def test_generated_three_case_pdf_embeds_fonts_and_keeps_footer_on_each_page(self) -> None:
        cases = [
            {
                "pathogen": f"测试病原{i}",
                "pathogen_latin": f"Test organism {i}",
                "case_id": f"QA-{i:02d}",
                "label": "无害",
                "confidence": "中",
                "patient_summary": "测试病例摘要。",
                "explanation": "结合病例信息和文献证据解释既有标签，不根据一般背景资料改变结论。",
                "mngs_evidence": ["检测样本为测试标本。", "检出信号较弱，需结合临床复核。"],
                "clinical_match": "现有资料未提供目标物种在当前病例中的直接证据。",
                "references": ["PubMed 与 UpToDate 背景资料仅作为证据边界说明。"],
                "limitations": ["缺少独立方法验证。"],
                "review_items": ["结合临床表现和其他检验指标复核。"],
            }
            for i in range(1, 4)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "three-cases.pdf"
            renderer.build_explainability_pdf(
                output,
                cases=cases,
                title="三例可解释性报告",
                compact=True,
            )

            reader = PdfReader(str(output))
            self.assertEqual(4, len(reader.pages))
            for page in reader.pages:
                page_text = page.extract_text() or ""
                self.assertIn("不替代临床诊断或治疗决定", page_text)
                self.assertGreater(len(page_text.strip()), 80)

                fonts = page["/Resources"]["/Font"].get_object().values()
                embedded = False
                base_fonts = []
                for reference in fonts:
                    font = reference.get_object()
                    base_fonts.append(str(font.get("/BaseFont", "")))
                    descriptor = font.get("/FontDescriptor")
                    if descriptor is None and font.get("/DescendantFonts"):
                        descendant = font["/DescendantFonts"][0].get_object()
                        descriptor = descendant.get("/FontDescriptor")
                    if descriptor is not None:
                        descriptor = descriptor.get_object()
                        embedded |= any(
                            key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")
                        )
                self.assertTrue(embedded, "Each page must reference an embedded font")
                self.assertNotIn("/STSong-Light", base_fonts)


if __name__ == "__main__":
    unittest.main()
