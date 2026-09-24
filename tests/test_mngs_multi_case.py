from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cyber_doctor.mngs.rag_judge import judge_with_rag_stream, parse_mngs_cases, retrieve_mngs_evidence
from cyber_doctor.reporting.mngs_report import _parse_json_response, export_latest_chat_pdf


SAMPLE_PATH = Path(os.getenv(
    "MNGS_TEST_FIXTURE",
    r"E:\华大医疗agent资料\待判断的病原.md",
))


class _FakeClient:
    def chat_with_ai(self, prompt: str) -> str:
        return json.dumps(
            {
                "label": "有害",
                "confidence": "高",
                "patient_summary": "测试患者摘要",
                "mngs_evidence": ["测试检出证据"],
                "clinical_match": "测试临床匹配",
                "explanation": "测试解释",
                "evidence": [],
                "limitations": ["测试证据局限"],
                "review_items": ["测试复核项"],
            },
            ensure_ascii=False,
        )


class _FakeFactory:
    def get_client(self) -> _FakeClient:
        return _FakeClient()


@unittest.skipUnless(SAMPLE_PATH.exists(), "local three-case fixture is unavailable")
class MultiCasePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.input_text = SAMPLE_PATH.read_text(encoding="utf-8")

    def test_jsonl_parser_keeps_all_three_cases(self) -> None:
        cases = parse_mngs_cases(self.input_text)
        self.assertEqual(3, len(cases))
        self.assertEqual(
            ["23B2024080804", "23B2023122304", "23B2023091904"],
            [case.case_id for case in cases],
        )
        self.assertEqual(["无害", "无害", "无害"], [case.existing_label for case in cases])

    def test_jsonl_parser_accepts_uploaded_text_prefix(self) -> None:
        prefixed = "\n".join(f"文本{i}内容：{line}" for i, line in enumerate(self.input_text.splitlines(), 1))
        self.assertEqual(3, len(parse_mngs_cases(prefixed)))

    def test_name_id_related_documents_are_not_called_direct_species_evidence(self) -> None:
        evidence = retrieve_mngs_evidence(parse_mngs_cases(self.input_text)[0])
        self.assertTrue(evidence.docs)
        self.assertTrue(all(doc.metadata.get("support_scope") != "目标物种直接匹配" for doc in evidence.docs))

    @patch("cyber_doctor.client.client_factory.Clientfactory", _FakeFactory)
    def test_analysis_preserves_count_order_and_existing_labels(self) -> None:
        response = "".join(judge_with_rag_stream(self.input_text))
        payload = _parse_json_response(response)
        results = payload["cases"]
        self.assertEqual(3, len(results))
        self.assertEqual(
            ["23B2024080804", "23B2023122304", "23B2023091904"],
            [item["UUID"] for item in results],
        )
        self.assertEqual(["无害", "无害", "无害"], [item["label"] for item in results])

    @patch("cyber_doctor.client.client_factory.Clientfactory", _FakeFactory)
    def test_aggregate_result_exports_one_pdf_with_all_cases(self) -> None:
        response = "".join(judge_with_rag_stream(self.input_text))
        history = [[self.input_text, response]]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "three-cases.pdf"
            exported = Path(export_latest_chat_pdf(str(output), history))
            self.assertTrue(exported.exists())
            self.assertGreater(exported.stat().st_size, 0)
            from pypdf import PdfReader

            self.assertGreaterEqual(len(PdfReader(str(exported)).pages), 4)


if __name__ == "__main__":
    unittest.main()
