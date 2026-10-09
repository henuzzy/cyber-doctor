"""Escaped HTML presentation of existing report state, without changing facts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Mapping

from cyber_doctor.mngs.session import active_report_session


HEADER = """<header class="brandbar">
  <div class="brand-lockup"><span class="brand-mark">CD</span><div>
    <div class="brand-name">Cyber Doctor <span>mNGS</span></div>
    <p>临床解释与医生审阅工作台</p>
  </div></div>
  <div class="brand-caption">证据可追溯 <span>·</span> 意见有记录 <span>·</span> 报告有版本</div>
</header>"""

EMPTY_REPORT = """<section class="empty-report">
  <span class="eyebrow">EXPLAINABILITY REPORT</span>
  <h2>从病例到可复核的解释</h2>
  <p>提交 mNGS 判定结果，查看病例与文献证据，<br>再通过医生意见完善报告。</p>
  <div class="empty-steps">
    <div><span class="step-number">01</span><strong>提交病例</strong><p>粘贴病例或上传文件<br>支持一份输入包含多个病原</p></div>
    <div><span class="step-number">02</span><strong>阅读证据</strong><p>逐例查看解释与临床匹配<br>展开检测指标和知识库引用</p></div>
    <div><span class="step-number">03</span><strong>审阅与确认</strong><p>提交医生意见生成修订版<br>确认后下载 PDF 报告</p></div>
  </div>
  <div class="empty-tip">先在「病例输入」中提交资料，报告将显示在这里。</div>
</section>"""

_FIELD_NAMES = {
    "source": "来源", "support_type": "证据关系", "summary": "摘要",
    "title": "标题", "pmid": "PMID", "doi": "DOI", "url": "链接",
    "sample_type": "取样部位", "reads": "种检出序列数", "coverage": "覆盖率",
    "abundance": "种相对丰度", "case_id": "病例编号",
}


def _text(value: Any, default: str = "未提供") -> str:
    if value is None or value == "" or value == [] or value == {}:
        return escape(default)
    if isinstance(value, Mapping):
        return "；".join(
            f"{escape(_FIELD_NAMES.get(str(key), str(key)))}：{_text(item)}"
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "；".join(_text(item) for item in value)
    return escape(str(value)).replace("\n", "<br>")


def _list(value: Any) -> str:
    if not value:
        return '<p class="muted">未提供</p>'
    values = value if isinstance(value, (list, tuple)) else [value]
    return '<ul class="report-list">' + "".join(f"<li>{_text(item)}</li>" for item in values) + "</ul>"


def format_session_time(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return _text(value)


def render_overview(workspace: Mapping[str, Any] | None) -> str:
    session = active_report_session(workspace)
    cases = (session or {}).get("cases", [])
    values = [
        ("报告会话", len((workspace or {}).get("sessions", []))),
        ("当前病原", len(cases)),
        ("报告版本", f"v{session.get('version', 1)}" if session else "—"),
        ("审阅状态", "已确认" if (session or {}).get("status") == "confirmed" else "待审阅" if session else "待开始"),
    ]
    return '<div class="overview">' + "".join(
        f'<div class="overview-item"><span>{label}</span><strong>{_text(value)}</strong></div>'
        for label, value in values
    ) + "</div>"


def render_session_note(workspace: Mapping[str, Any] | None) -> str:
    session = active_report_session(workspace)
    if not session:
        return '<div class="session-note"><span class="eyebrow">当前会话</span><p>提交病例后，报告会话会自动创建。</p></div>'
    status = "已确认" if session.get("status") == "confirmed" else "待医生审阅"
    return (
        '<div class="session-note"><span class="eyebrow">当前会话</span>'
        f'<strong>{_text(session.get("title"))}</strong>'
        f'<p>{len(session.get("cases", []))} 个病原 · 第 {int(session.get("version", 1))} 版 · {status}</p></div>'
    )


def render_report(workspace: Mapping[str, Any] | None) -> str:
    session = active_report_session(workspace)
    if not session or not session.get("cases"):
        return EMPTY_REPORT
    status = "医生已确认" if session.get("status") == "confirmed" else "待医生审阅"
    version = int(session.get("version") or 1)
    parts = [
        '<div class="report-heading"><div><span class="eyebrow">EXPLAINABILITY REPORT</span>'
        f'<h2>{_text(session.get("title"))}</h2><p>第 {version} 版 · {len(session["cases"])} 个病原</p></div>'
        f'<span class="status-badge {"is-confirmed" if session.get("status") == "confirmed" else ""}">{status}</span></div>'
    ]
    for index, case in enumerate(session["cases"], 1):
        label = case.get("label") or case.get("Label") or "未提供"
        tone = {"有害": "label-harmful", "无害": "label-harmless"}.get(str(label), "label-neutral")
        raw = case.get("raw_case") or {}
        identity = case.get("session_case_id") or case.get("UUID") or raw.get("case_id") or "未提供"
        parts.append(
            '<article class="case-card"><div class="case-heading"><div>'
            f'<span class="case-index">病原 {index:02d}</span><h3>{_text(case.get("Chinese") or case.get("Latin"))}</h3>'
            f'<p class="case-latin">{_text(case.get("Latin"), "")}</p></div>'
            f'<span class="judgement {tone}">既有判定 · {_text(label)}</span></div>'
            f'<div class="case-meta"><span>编号：{_text(identity)}</span><span>解释置信度：{_text(case.get("confidence"))}</span></div>'
            '<div class="report-section"><h4>判定解释</h4>'
            f'<p>{_text(case.get("explanation"))}</p></div>'
            '<div class="report-section"><h4>病例摘要</h4>'
            f'<p>{_text(case.get("patient_summary"))}</p></div>'
            '<div class="report-section"><h4>临床证据匹配</h4>'
            f'<p>{_text(case.get("clinical_match"))}</p></div>'
            '<details class="evidence-detail"><summary>mNGS 检出证据</summary>'
            f'{_list(case.get("mngs_evidence"))}</details>'
            '<details class="evidence-detail"><summary>知识库引用与证据</summary>'
            f'{_list(case.get("evidence") or case.get("references"))}</details>'
            '<div class="review-points"><div><h4>证据局限</h4>'
            f'{_list(case.get("limitations"))}</div><div><h4>建议复核项</h4>'
            f'{_list(case.get("review_items"))}</div></div>'
        )
        if case.get("doctor_feedback"):
            parts.append('<div class="doctor-note"><h4>已纳入的医生意见</h4>' + _list(case["doctor_feedback"]) + '</div>')
        parts.append('</article>')
    return "".join(parts)


def render_processing_report(workspace: Mapping[str, Any] | None, message: str = "正在读取病例资料，请稍候。") -> str:
    return (
        '<div class="processing-note" role="status"><strong>正在生成</strong>'
        f'<span>{_text(message)}</span></div>' + render_report(workspace)
    )


def render_history(workspace: Mapping[str, Any] | None) -> str:
    session = active_report_session(workspace)
    if not session:
        return '<div class="history-empty"><h3>审阅记录将保存在这里</h3><p>每次修订、确认和重新打开审阅都会记录在当前报告会话中。</p></div>'
    parts = ['<div class="history-heading"><span class="eyebrow">REVIEW HISTORY</span><h2>报告版本与审阅记录</h2></div>']
    for snapshot in reversed(session.get("versions", [])):
        status = "医生已确认" if snapshot.get("status") == "confirmed" else "审阅中"
        parts.append(
            '<article class="history-card">'
            f'<div><strong>第 {int(snapshot.get("version") or 1)} 版</strong><span>{status} · {format_session_time(snapshot.get("created_at"))}</span></div>'
            f'<p>{_text(snapshot.get("summary"))}</p></article>'
        )
    events = session.get("review_events", [])
    if events:
        parts.append('<h3 class="event-heading">医生操作记录</h3>')
    for event in reversed(events):
        event_type = {"confirm": "确认报告", "reopen": "重新打开审阅"}.get(event.get("type"), event.get("type") or "医生意见")
        targets = "、".join(str(item) for item in event.get("target_case_ids", [])) or "整份报告"
        parts.append(
            '<article class="history-card">'
            f'<div><strong>{_text(event_type)}</strong><span>{format_session_time(event.get("created_at"))}</span></div>'
            f'<p>{_text(event.get("content"))}</p>'
            f'<small>第 {int(event.get("based_on_version") or 1)} 版 → 第 {int(event.get("result_version") or 1)} 版 · {_text(event.get("target_section"))} · {_text(targets)}</small>'
            '</article>'
        )
    return "".join(parts)
