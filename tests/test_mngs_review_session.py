from __future__ import annotations

import json
import unittest

from cyber_doctor.mngs.rag_judge import revise_judgements_with_doctor_feedback
from cyber_doctor.mngs.session import (
    active_report_session,
    add_report_session,
    append_report_revision,
    confirm_report_session,
    create_report_session,
    create_report_workspace,
    reopen_report_session,
    select_report_session,
)
from cyber_doctor.reporting.mngs_report import case_from_judgement


class _FakeClient:
    def __init__(self):
        self.prompt = ""

    def chat_with_ai(self, prompt: str) -> str:
        self.prompt = prompt
        return json.dumps({
            "label": "有害",
            "confidence": "高",
            "patient_summary": "模型尝试改写的摘要",
            "mngs_evidence": ["模型尝试替换的 reads"],
            "clinical_match": "已纳入医生补充的发热信息。",
            "explanation": "根据病例信息及医生意见重新说明既有判定。",
            "evidence": [{"source": "虚构来源", "summary": "不应采用"}],
            "limitations": ["仍需结合完整临床资料复核。"],
            "review_items": ["核对医生补充的症状信息。"],
        }, ensure_ascii=False)


class MNGSReviewSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = {
            "session_case_id": "case-001",
            "UUID": "case-001",
            "Chinese": "测试病原",
            "label": "无害",
            "confidence": "中",
            "patient_summary": "原始病例摘要",
            "mngs_evidence": ["reads=12"],
            "clinical_match": "初版匹配说明",
            "explanation": "初版解释",
            "evidence": [{"source": "真实文献", "summary": "初版证据"}],
            "limitations": ["初版局限"],
            "review_items": ["初版复核项"],
            "raw_case": {"case_id": "case-001", "sample_type": "肺泡灌洗液"},
        }

    def test_doctor_revision_preserves_label_facts_and_citations(self) -> None:
        client = _FakeClient()
        revised = revise_judgements_with_doctor_feedback(
            [self.case],
            "患者近期持续发热，医生补充病史。",
            feedback_type="补充事实",
            target_section="临床匹配",
            client=client,
        )[0]

        self.assertEqual("无害", revised["label"])
        self.assertEqual(self.case["mngs_evidence"], revised["mngs_evidence"])
        self.assertEqual(self.case["patient_summary"], revised["patient_summary"])
        self.assertEqual(self.case["evidence"], revised["evidence"])
        self.assertIn("医生意见类型：补充事实", client.prompt)
        self.assertIn("关联章节：临床匹配", client.prompt)
        self.assertEqual(["患者近期持续发热，医生补充病史。"], revised["doctor_feedback"])

    def test_session_keeps_immutable_versions_review_event_and_confirmation(self) -> None:
        initial = create_report_session([self.case])
        updated_case = {**self.case, "explanation": "修订后的解释"}
        revised = append_report_revision(
            initial,
            [updated_case],
            feedback="补充近期发热病史",
            feedback_type="补充事实",
            target_section="临床匹配",
            target_case_ids=["case-001"],
        )
        confirmed = confirm_report_session(revised)

        self.assertEqual("初版解释", revised["versions"][0]["cases"][0]["explanation"])
        self.assertEqual("修订后的解释", revised["versions"][1]["cases"][0]["explanation"])
        self.assertEqual("补充事实", revised["review_events"][0]["type"])
        self.assertEqual("confirmed", confirmed["status"])
        self.assertEqual(2, confirmed["confirmed_version"])
        self.assertEqual("in_review", initial["status"])

    def test_empty_feedback_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "请先填写医生审阅意见"):
            revise_judgements_with_doctor_feedback([self.case], "   ", client=_FakeClient())

    def test_pdf_adapter_carries_doctor_feedback_and_report_version(self) -> None:
        case = case_from_judgement({**self.case, "doctor_feedback": ["核对症状"], "report_version": 3})
        self.assertEqual(["核对症状"], case["doctor_feedback"])
        self.assertEqual(3, case["report_version"])

    def test_workspace_keeps_multiple_report_sessions_isolated(self) -> None:
        workspace = create_report_workspace()
        first = create_report_session([self.case])
        workspace = add_report_session(workspace, first)
        second_case = {**self.case, "session_case_id": "case-002", "UUID": "case-002", "Chinese": "第二个病原"}
        second = create_report_session([second_case])
        workspace = add_report_session(workspace, second)

        self.assertEqual(2, len(workspace["sessions"]))
        self.assertEqual(second["session_id"], active_report_session(workspace)["session_id"])
        switched = select_report_session(workspace, first["session_id"])
        self.assertEqual("case-001", active_report_session(switched)["cases"][0]["session_case_id"])
        self.assertEqual("case-002", active_report_session(workspace)["cases"][0]["session_case_id"])

    def test_confirmed_session_can_be_reopened_without_rewriting_report(self) -> None:
        session = create_report_session([self.case])
        confirmed = confirm_report_session(session)
        reopened = reopen_report_session(confirmed)

        self.assertEqual("confirmed", confirmed["status"])
        self.assertEqual("in_review", reopened["status"])
        self.assertEqual(session["cases"], reopened["cases"])
        self.assertEqual("reopen", reopened["review_events"][-1]["type"])


if __name__ == "__main__":
    unittest.main()
