from cyber_doctor.mngs.session import (
    add_report_session,
    append_report_revision,
    create_report_session,
    create_report_workspace,
)
from cyber_doctor.ui.workbench import render_history, render_overview, render_processing_report, render_report


def _workspace(case):
    return add_report_session(create_report_workspace(), create_report_session([case]))


def test_report_escapes_untrusted_case_and_model_content():
    attack = '<img src=x onerror="alert(1)">'
    workspace = _workspace({
        "Chinese": attack, "label": "无害", "explanation": attack,
        "evidence": [{"source": attack, "summary": attack}],
        "doctor_feedback": [attack],
    })
    before = repr(workspace)
    rendered = render_report(workspace)
    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert "既有判定 · 无害" in rendered
    assert repr(workspace) == before
    assert "<img" not in render_processing_report(workspace, attack)


def test_report_keeps_evidence_and_all_review_points_visible():
    workspace = _workspace({
        "Chinese": "测试病原", "label": "有害",
        "mngs_evidence": ["reads=12", "覆盖率=0.4"],
        "evidence": [{"source": "PubMed 123", "support_type": "间接证据", "summary": "引用摘要"}],
        "limitations": ["第一项局限", "第二项局限"],
        "review_items": ["核对检测", "核对症状"],
    })
    rendered = render_report(workspace)
    for value in ["reads=12", "覆盖率=0.4", "PubMed 123", "间接证据", "引用摘要", "第一项局限", "第二项局限", "核对检测", "核对症状"]:
        assert value in rendered
    assert "['" not in rendered


def test_history_preserves_review_scope_and_escapes_feedback():
    session = create_report_session([{"Chinese": "测试病原", "label": "无害"}])
    revised = append_report_revision(
        session, session["cases"], feedback="<script>意见</script>",
        feedback_type="质疑证据", target_section="知识库证据", target_case_ids=["case-001"],
    )
    workspace = add_report_session(create_report_workspace(), revised)
    history = render_history(workspace)
    assert "<script>" not in history
    assert "&lt;script&gt;" in history
    assert "case-001" in history
    assert "知识库证据" in history
    assert "第 1 版 → 第 2 版" in history
    assert "v2" in render_overview(workspace)
