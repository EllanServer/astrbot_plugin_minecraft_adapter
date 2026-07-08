"""Structured five-section report contract for MineSentinel."""

from __future__ import annotations

from typing import Any


MAX_SECTION_BULLETS = 8
MAX_SECTION_BULLET_CHARS = 220

REPORT_SECTION_SPECS = (
    ("overall", "一、整体情况"),
    ("incidents", "二、重点事件总结"),
    ("community", "三、聊天与社区观察"),
    ("player_problems", "四、玩家问题/投诉识别"),
    ("risk_actions", "五、风险提醒与建议处理"),
)


def normalize_report_sections(
    raw_sections: Any,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return all five sections in stable order with compact text bullets."""
    incoming = _sections_by_id(raw_sections)
    sections: list[dict[str, Any]] = []
    for section_id, title in REPORT_SECTION_SPECS:
        raw = incoming.get(section_id) or {}
        bullets = _clean_bullets(
            raw.get("bullets") or raw.get("items") or raw.get("lines") or []
        )
        if not bullets:
            bullets = _fallback_bullets(section_id, report)
        sections.append(
            {
                "id": section_id,
                "title": title,
                "bullets": bullets[:MAX_SECTION_BULLETS],
            }
        )
    return sections


def build_report_sections(report: dict[str, Any]) -> list[dict[str, Any]]:
    return normalize_report_sections([], report)


def _sections_by_id(raw_sections: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_sections, list):
        return {}
    sections: dict[str, dict[str, Any]] = {}
    valid_ids = {section_id for section_id, _ in REPORT_SECTION_SPECS}
    for raw in raw_sections:
        if not isinstance(raw, dict):
            continue
        section_id = str(raw.get("id") or "").strip()
        if section_id in valid_ids and section_id not in sections:
            sections[section_id] = raw
    return sections


def _fallback_bullets(section_id: str, report: dict[str, Any]) -> list[str]:
    if section_id == "overall":
        return _overall_bullets(report)
    if section_id == "incidents":
        return _incident_bullets(report)
    if section_id == "community":
        return _community_bullets(report)
    if section_id == "player_problems":
        return _player_problem_bullets(report)
    if section_id == "risk_actions":
        return _risk_action_bullets(report)
    return []


def _overall_bullets(report: dict[str, Any]) -> list[str]:
    bullets = _clean_bullets([report.get("summary")])
    log_count = report.get("log_count")
    servers = ", ".join(str(server) for server in (report.get("servers") or []) if server)
    if log_count is not None:
        suffix = f"；服务器：{servers}" if servers else ""
        bullets.append(_truncate(f"日志观察 {log_count} 条{suffix}。"))
    return bullets or ["本窗口暂无可汇总的服务器运行日志。"]


def _incident_bullets(report: dict[str, Any]) -> list[str]:
    findings = report.get("incident_findings") or []
    bullets = _clean_bullets(findings)
    if bullets:
        return bullets
    for issue in report.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        title = str(issue.get("incident_title") or issue.get("tag") or issue.get("category") or "").strip()
        action = str(issue.get("suggested_action") or "").strip()
        if title and action:
            bullets.append(_truncate(f"{title}：{action}"))
        elif title:
            bullets.append(_truncate(title))
    return bullets[:MAX_SECTION_BULLETS] or ["未发现需要立即处理的重点事件。"]


def _community_bullets(report: dict[str, Any]) -> list[str]:
    bullets = _clean_bullets([report.get("chat_summary")])
    categories = report.get("categories") or {}
    for key in ("community", "chat_review", "community_ops"):
        bullets.extend(_clean_bullets((categories.get(key) or [])[:3]))
    return bullets[:MAX_SECTION_BULLETS] or ["本窗口未发现明显聊天或社区运营异常。"]


def _player_problem_bullets(report: dict[str, Any]) -> list[str]:
    categories = report.get("categories") or {}
    bullets: list[str] = []
    for key in ("complaint", "player_feedback", "suggestion"):
        bullets.extend(_clean_bullets((categories.get(key) or [])[:4]))
    return bullets[:MAX_SECTION_BULLETS] or ["本窗口未发现集中玩家投诉或待跟进反馈。"]


def _risk_action_bullets(report: dict[str, Any]) -> list[str]:
    bullets = _clean_bullets(report.get("ops_notes") or [])
    for issue in report.get("issues") or []:
        if isinstance(issue, dict):
            bullets.extend(_clean_bullets([issue.get("suggested_action")]))
    return _dedupe(bullets)[:MAX_SECTION_BULLETS] or ["保持观察，无需立即执行管理动作。"]


def _clean_bullets(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    bullets: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("text") or value.get("summary") or value.get("title")
        text = _truncate(str(value or "").strip())
        if text:
            bullets.append(text)
    return _dedupe(bullets)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = " ".join(value.lower().split())
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _truncate(value: str) -> str:
    if len(value) <= MAX_SECTION_BULLET_CHARS:
        return value
    return value[: MAX_SECTION_BULLET_CHARS - 3] + "..."
