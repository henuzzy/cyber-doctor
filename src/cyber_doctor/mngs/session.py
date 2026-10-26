"""JSON-serializable mNGS report session state for a single Gradio session."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_report_session(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Create an isolated review session with immutable report snapshots."""
    if not cases:
        raise ValueError("至少需要一个 mNGS 病例才能创建报告会话")
    report_cases = deepcopy([dict(case) for case in cases])
    for case in report_cases:
        case.setdefault("doctor_feedback", [])
        case["report_version"] = 1
    case_names = [str(case.get("Chinese") or case.get("Latin") or case.get("NameID") or "病例") for case in report_cases]
    title = "、".join(case_names[:2]) + (f"等 {len(case_names)} 例" if len(case_names) > 2 else "")
    created_at = _now()
    return {
        "session_id": uuid4().hex,
        "title": title[:100],
        "status": "in_review",
        "version": 1,
        "created_at": created_at,
        "updated_at": created_at,
        "confirmed_version": None,
        "cases": report_cases,
        "review_events": [],
        "versions": [{
            "version": 1,
            "status": "in_review",
            "created_at": created_at,
            "summary": "AI 初版报告",
            "cases": deepcopy(report_cases),
        }],
    }


def create_report_workspace() -> dict[str, Any]:
    """Create an empty report workspace scoped to the current browser session."""
    return {"sessions": [], "active_session_id": None}


def add_report_session(workspace: Mapping[str, Any] | None, session: Mapping[str, Any]) -> dict[str, Any]:
    """Append a new case-report session without discarding earlier sessions."""
    updated = deepcopy(dict(workspace or create_report_workspace()))
    sessions = [item for item in updated.get("sessions", []) if item.get("session_id") != session.get("session_id")]
    sessions.append(deepcopy(dict(session)))
    updated.update({"sessions": sessions, "active_session_id": session.get("session_id")})
    return updated


def active_report_session(workspace: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not workspace:
        return None
    active_id = workspace.get("active_session_id")
    return next(
        (item for item in workspace.get("sessions", []) if item.get("session_id") == active_id),
        None,
    )


def update_report_session(workspace: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    """Replace one session snapshot in the workspace while retaining its peers."""
    updated = deepcopy(dict(workspace))
    updated["sessions"] = [
        deepcopy(dict(session)) if item.get("session_id") == session.get("session_id") else item
        for item in updated.get("sessions", [])
    ]
    return updated


def select_report_session(workspace: Mapping[str, Any], session_id: str) -> dict[str, Any]:
    updated = deepcopy(dict(workspace))
    if not any(item.get("session_id") == session_id for item in updated.get("sessions", [])):
        raise ValueError("找不到所选的报告会话")
    updated["active_session_id"] = session_id
    return updated


def append_report_revision(
    session: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    *,
    feedback: str,
    feedback_type: str,
    target_section: str,
    target_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Append an auditable doctor review and a complete report snapshot."""
    updated = deepcopy(dict(session))
    version = int(updated.get("version") or 1) + 1
    timestamp = _now()
    report_cases = deepcopy([dict(case) for case in cases])
    for case in report_cases:
        case["report_version"] = version
    event = {
        "review_id": uuid4().hex,
        "created_at": timestamp,
        "type": feedback_type,
        "target_section": target_section,
        "target_case_ids": list(target_case_ids),
        "content": feedback.strip(),
        "based_on_version": int(updated.get("version") or 1),
        "result_version": version,
    }
    updated.update({
        "status": "in_review",
        "version": version,
        "updated_at": timestamp,
        "confirmed_version": None,
        "cases": report_cases,
        "review_events": [*updated.get("review_events", []), event],
        "versions": [
            *updated.get("versions", []),
            {
                "version": version,
                "status": "in_review",
                "created_at": timestamp,
                "summary": f"医生意见：{feedback.strip()[:100]}",
                "review_id": event["review_id"],
                "cases": deepcopy(report_cases),
            },
        ],
    })
    return updated


def confirm_report_session(session: Mapping[str, Any]) -> dict[str, Any]:
    """Mark the current version as doctor-confirmed without altering its content."""
    updated = deepcopy(dict(session))
    version = int(updated.get("version") or 1)
    timestamp = _now()
    updated.update({
        "status": "confirmed",
        "confirmed_version": version,
        "updated_at": timestamp,
        "review_events": [
            *updated.get("review_events", []),
            {
                "review_id": uuid4().hex,
                "created_at": timestamp,
                "type": "confirm",
                "target_section": "整份报告",
                "target_case_ids": [],
                "content": f"医生确认第 {version} 版报告",
                "based_on_version": version,
                "result_version": version,
            },
        ],
    })
    for item in updated.get("versions", []):
        if item.get("version") == version:
            item["status"] = "confirmed"
    return updated


def reopen_report_session(session: Mapping[str, Any]) -> dict[str, Any]:
    """Return a confirmed report to review without changing its current version."""
    updated = deepcopy(dict(session))
    if updated.get("status") != "confirmed":
        raise ValueError("只有已确认的报告可以重新打开审阅")
    version = int(updated.get("version") or 1)
    timestamp = _now()
    updated.update({"status": "in_review", "confirmed_version": None, "updated_at": timestamp})
    updated["review_events"] = [
        *updated.get("review_events", []),
        {
            "review_id": uuid4().hex,
            "created_at": timestamp,
            "type": "reopen",
            "target_section": "整份报告",
            "target_case_ids": [],
            "content": f"重新打开第 {version} 版报告审阅",
            "based_on_version": version,
            "result_version": version,
        },
    ]
    for item in updated.get("versions", []):
        if item.get("version") == version:
            item["status"] = "in_review"
    return updated
