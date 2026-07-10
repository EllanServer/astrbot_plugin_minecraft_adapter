"""Incident-management view model for MineSentinel reports.

The heuristic/AI report remains the factual ledger.  This module derives an
action-first, versioned presentation contract without allowing presentation
state to mutate evidence, severity, or incident membership.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from .incident_format import format_time_window, resolve_attachment_name
from .incidents import IncidentGroup, issue_sort_key
from .incident_response import (
    build_check_plan,
    build_incident_facts,
    build_reader_action,
    build_reader_verification,
    format_check_step,
)
from .labels import action_label, action_timing, impact_label
from .presentation import ReportPresentationBuilder


SCHEMA_VERSION = 3
CONTINUITY_GAP_MS = 30 * 60 * 1000
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
REPORT_TYPE_LABELS = {
    "inspection": "即时巡检",
    "periodic": "周期巡检",
    "postmortem": "事件复盘",
}
LIFECYCLE_LABELS = {
    "new": "新发",
    "ongoing": "持续",
    "recovered": "已恢复",
}


class IncidentLifecycleStore:
    """Persist the last active incident set per delivery scope.

    The file is intentionally small and contains summaries only. Raw evidence
    stays in the existing observation/export stores.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def active_for(self, scope: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            data = self._read()
            active = (data.get("scopes") or {}).get(scope, {}).get("active") or {}
            return {
                str(key): dict(value)
                for key, value in active.items()
                if isinstance(value, dict)
            }

    def replace_active(
        self,
        scope: str,
        active: dict[str, dict[str, Any]],
        updated_at: int,
    ) -> None:
        with self._lock:
            data = self._read()
            scopes = data.setdefault("scopes", {})
            scopes[scope] = {
                "updated_at": updated_at,
                "active": active,
            }
            data["version"] = 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "scopes": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"version": 1, "scopes": {}}
        return data if isinstance(data, dict) else {"version": 1, "scopes": {}}


class IncidentManagementBuilder:
    """Derive the v3 incident-management contract from deterministic facts."""

    def __init__(
        self,
        lifecycle_store: IncidentLifecycleStore | None = None,
        max_summary_incidents: int = 6,
    ):
        self.lifecycle_store = lifecycle_store
        self.max_summary_incidents = max(1, max_summary_incidents)
        self.presentation_builder = ReportPresentationBuilder()

    def attach(
        self,
        report: dict[str, Any],
        total_count: int,
        dedupe_count: int,
        unique_players: int,
        *,
        report_type: str = "inspection",
        state_scope: str = "default",
        persist_state: bool = False,
    ) -> dict[str, Any]:
        report["incident_management"] = self.build(
            report,
            total_count,
            dedupe_count,
            unique_players,
            report_type=report_type,
            state_scope=state_scope,
            persist_state=persist_state,
        )
        return report

    def build(
        self,
        report: dict[str, Any],
        total_count: int,
        dedupe_count: int,
        unique_players: int,
        *,
        report_type: str = "inspection",
        state_scope: str = "default",
        persist_state: bool = False,
    ) -> dict[str, Any]:
        # Import lazily: text_renderer owns nuanced domain wording, while this
        # module owns the structured lifecycle/action contract.
        from . import text_renderer as legacy

        presentation = self.presentation_builder.build(
            report,
            total_count,
            dedupe_count,
            unique_players,
        )
        incident_groups, observation_groups = legacy._split_incident_groups(
            presentation.incidents
        )
        previous = (
            self.lifecycle_store.active_for(state_scope)
            if self.lifecycle_store
            else {}
        )
        current_state: dict[str, dict[str, Any]] = {}
        signature_occurrences: dict[str, int] = {}
        incidents: list[dict[str, Any]] = []

        for group in incident_groups:
            signature = _incident_signature(group)
            signature_occurrences[signature] = signature_occurrences.get(signature, 0) + 1
            occurrence = signature_occurrences[signature]
            state_key = signature if occurrence == 1 else f"{signature}:{occurrence}"
            prior = previous.get(state_key) or previous.get(signature)
            lifecycle = _lifecycle_for(group, prior)
            incident = _managed_incident(group, state_key, lifecycle, legacy)
            incidents.append(incident)
            current_state[state_key] = _state_record(incident, group)

        recovered: list[dict[str, Any]] = []
        if persist_state:
            for state_key, prior in previous.items():
                if state_key in current_state:
                    continue
                recovered.append(_recovered_incident(state_key, prior))
            if self.lifecycle_store:
                self.lifecycle_store.replace_active(
                    state_scope,
                    current_state,
                    _report_end(report),
                )

        incidents.extend(recovered)
        incidents.sort(key=_managed_incident_sort_key)
        action_queue = _action_queue(incidents)[
            : min(3, self.max_summary_incidents)
        ]
        counts = _lifecycle_counts(incidents)
        status, status_level = _management_status(incidents)
        attachment_name = resolve_attachment_name(report)
        truncated = any("有界样本" in str(note) for note in report.get("ops_notes") or [])
        observation_count = len(observation_groups) + len(
            legacy._category_observation_lines(report)
        )

        result = {
            "schema_version": SCHEMA_VERSION,
            "report_type": report_type,
            "report_type_label": REPORT_TYPE_LABELS.get(report_type, "巡检报告"),
            "status": status,
            "status_level": status_level,
            "summary": _management_summary(
                report_type,
                incidents,
                counts,
                observation_count,
            ),
            "time_window": format_time_window(report),
            "servers": _format_servers(report),
            "counts": counts,
            "action_queue": action_queue,
            "summary_incident_limit": self.max_summary_incidents,
            "incidents": incidents,
            "observations": {
                "count": observation_count,
                "summary": _observation_summary(report, observation_count),
            },
            "coverage": {
                "records": total_count,
                "deduplicated": dedupe_count,
                "players": unique_players,
                "analysis_truncated": truncated,
                "attachment": attachment_name,
                "evidence_source": "自动分析结果和相关时间前后的原始日志",
            },
            "next_update": _next_update(action_queue, report_type),
            "legacy_sections": report.get("report_sections") or [],
        }
        return result


def format_incident_management_text(report: dict[str, Any]) -> str:
    """Render the v3 contract as a compact action-first text fallback."""

    management = report.get("incident_management") or {}
    counts = management.get("counts") or {}
    coverage = management.get("coverage") or {}
    action_heading = (
        "复盘后要做什么"
        if management.get("report_type") == "postmortem"
        else "接下来要做什么"
    )
    lines = [
        f"MineSentinel {management.get('report_type_label') or '事件管理报告'}",
        f"状态：{management.get('status') or '待确认'}",
        f"范围：{management.get('servers') or '全部服务器'} · {management.get('time_window') or '未知窗口'}",
        "",
        "管理结论",
        str(management.get("summary") or "当前没有可汇总的管理结论。"),
        "",
        action_heading,
    ]
    actions = management.get("action_queue") or []
    if actions:
        for index, action in enumerate(actions, 1):
            lines.append(
                f"{index}. [{action.get('action_label', '留意观察')} · "
                f"{action.get('timing', '下次巡检时')}] {action.get('incident_id', 'INC')} · "
                f"{action.get('time') or '时间未记录'} · {action.get('where') or '位置未记录'}"
            )
            lines.append(
                f"   人物：{action.get('people') or '未关联具体玩家'}；"
                f"插件/组件：{action.get('components') or '未识别'}"
            )
            lines.append(
                f"   操作：{str(action.get('action') or '复核事件').rstrip('。；;')}；"
                f"负责人：{action.get('owner_role') or '值班管理员'}；"
                f"完成标准：{action.get('verification') or '确认风险不再持续'}"
            )
    else:
        lines.append("1. 无需立即处置，按既定周期继续观察。")

    lines.extend(
        [
            "",
            "处理进度",
            (
                f"新发 {counts.get('new', 0)} · 持续 {counts.get('ongoing', 0)} · "
                f"已恢复 {counts.get('recovered', 0)} · 待复核 {counts.get('needs_review', 0)}"
            ),
            "",
            "问题列表",
        ]
    )
    incidents = management.get("incidents") or []
    try:
        summary_limit = max(1, int(management.get("summary_incident_limit") or 6))
    except (TypeError, ValueError):
        summary_limit = 6
    if incidents:
        for incident in incidents[:summary_limit]:
            lines.append(
                f"- [{incident.get('action_label', '留意观察')}][{incident.get('lifecycle_label', '待确认')}] "
                f"{incident.get('incident_id', 'INC')} {incident.get('title', '运行事件')}"
            )
            facts = incident.get("facts") or {}
            lines.extend(
                [
                    f"  时间：{facts.get('time') or incident.get('time_range') or '未记录'}（{facts.get('duration') or '时长未知'}）",
                    f"  地点：{facts.get('where') or '未记录具体服务器/世界位置'}",
                    f"  人物：{facts.get('people_text') or '未关联具体玩家'}",
                    f"  插件/组件：{facts.get('components') or '未从证据识别'}",
                    f"  日志：{'、'.join(facts.get('log_files') or []) or '未记录文件名'}；相关记录 {incident.get('evidence_count', 0)} 条",
                    f"  结论：{incident.get('assessment') or '待人工复核'}",
                ]
            )
            if incident.get("ai_reviewed") and incident.get("recommended_action"):
                lines.append(
                    f"  AI 复核建议：{incident.get('recommended_action')}"
                )
            check_plan = incident.get("check_plan") or []
            if check_plan:
                lines.append(
                    "  AI 给出的检查与解决方案："
                    if incident.get("ai_plan_used")
                    else "  检查与解决方案："
                )
                for step_index, step in enumerate(check_plan, 1):
                    lines.append(f"    {step_index}. {format_check_step(step)}")
        if len(incidents) > summary_limit:
            lines.append(f"- 另有 {len(incidents) - summary_limit} 个事件请查看处置单或证据附件。")
    else:
        lines.append("- 本窗口没有需要升级为事件的异常。")

    lines.extend(
        [
            "",
            "报告依据与下次更新",
            (
                f"共检查 {coverage.get('records', 0)} 条日志记录，涉及玩家 {coverage.get('players', 0)} 人；"
                f"完整日志附件：{coverage.get('attachment') or '未生成'}。"
            ),
            f"下一次更新：{management.get('next_update') or '按既定巡检周期'}。",
        ]
    )
    return "\n".join(lines)


def _managed_incident(
    group: IncidentGroup,
    state_key: str,
    lifecycle: str,
    legacy: Any,
) -> dict[str, Any]:
    issues = list(group.issues)
    labels = legacy._incident_labels(issues)
    severity = str(group.max_severity or "low").lower()
    review_required = legacy._requires_manual_review(group)
    evidence_count = sum(_positive_int(issue.get("evidence_count"), 1) for issue in issues)
    evidence = legacy._incident_key_evidence(issues, limit=4)
    facts = build_incident_facts(
        issues,
        fallback_time_range=legacy._incident_time_text(group),
    )
    title = _specific_incident_title(
        group,
        legacy._incident_display_title(group, labels),
        facts,
    )
    ai_check_plan = _ai_check_plan(issues)
    check_plan = ai_check_plan or build_check_plan(issues, facts, group.family)
    if ai_check_plan:
        action = str(ai_check_plan[0].get("check") or "复核事件")
        verification = str(
            ai_check_plan[-1].get("expected")
            or ai_check_plan[-1].get("check")
            or "确认问题已经解决"
        )
    else:
        verification = build_reader_verification(issues, facts, group.family)
        action = build_reader_action(issues, facts, group.family)
    return {
        "incident_id": _incident_id(state_key),
        "state_key": state_key,
        "title": title,
        "family": group.family,
        "severity": severity,
        "action_label": action_label(severity),
        "impact_label": impact_label(severity),
        "lifecycle": lifecycle,
        "lifecycle_label": LIFECYCLE_LABELS.get(lifecycle, "待确认"),
        "needs_review": review_required,
        "facts": facts,
        "impact": legacy._impact_scope(group),
        "summary": legacy._incident_summary_sentence(group, labels),
        "assessment": legacy._incident_judgement_line(group),
        "ai_reviewed": any(bool(issue.get("ai_diagnosis")) for issue in issues),
        "ai_plan_used": bool(ai_check_plan),
        "check_plan_source": "ai" if ai_check_plan else "rules",
        "action_now": action,
        "recommended_action": legacy._incident_recommended_action(group),
        "action_next": _next_step_for(group),
        "check_plan": check_plan,
        "verification": verification,
        "owner_role": _owner_for(group),
        "evidence_count": evidence_count,
        "evidence": evidence,
        "time_range": legacy._incident_time_text(group),
        "first_seen_ts": int(group.start_ts or 0),
        "last_seen_ts": int(group.end_ts or 0),
        "research_sources": legacy._incident_research_sources(group),
    }


def _state_record(incident: dict[str, Any], group: IncidentGroup) -> dict[str, Any]:
    return {
        "incident_id": incident["incident_id"],
        "title": incident["title"],
        "family": incident["family"],
        "severity": incident["severity"],
        "action_label": incident["action_label"],
        "impact_label": incident["impact_label"],
        "impact": incident["impact"],
        "action_now": incident["action_now"],
        "verification": incident["verification"],
        "owner_role": incident["owner_role"],
        "first_seen_ts": int(group.start_ts or 0),
        "last_seen_ts": int(group.end_ts or 0),
        "evidence_count": incident["evidence_count"],
        "needs_review": incident["needs_review"],
        "facts": _state_facts(incident.get("facts") or {}),
        "check_plan": incident.get("check_plan") or [],
        "assessment": incident.get("assessment") or "",
        "ai_reviewed": bool(incident.get("ai_reviewed")),
        "ai_plan_used": bool(incident.get("ai_plan_used")),
        "check_plan_source": incident.get("check_plan_source") or "rules",
    }


def _ai_check_plan(issues: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Select the highest-priority validated AI plan for this incident."""

    for issue in sorted(issues, key=issue_sort_key):
        if not isinstance(issue.get("ai_diagnosis"), dict):
            continue
        raw = issue.get("ai_check_plan")
        if not isinstance(raw, list) or not raw:
            continue
        plan: list[dict[str, str]] = []
        for step in raw[:6]:
            if not isinstance(step, dict):
                plan = []
                break
            normalized = {
                field: str(step.get(field) or "").strip()
                for field in ("phase", "check", "expected", "on_failure")
            }
            if not all(normalized.values()):
                plan = []
                break
            plan.append(normalized)
        if len(plan) >= 2:
            return plan
    return []


def _specific_incident_title(
    group: IncidentGroup,
    fallback: str,
    facts: dict[str, Any],
) -> str:
    if group.family != "operations":
        return fallback
    plugins = list(facts.get("plugins") or [])
    subtypes = list(facts.get("subtypes") or [])
    categories = list(facts.get("categories") or [])
    if plugins:
        component_text = "、".join(plugins[:3])
        if len(plugins) > 3:
            component_text += " 等"
        problem_text = "、".join(subtypes[:3]) or "运行异常"
        return f"{component_text}：{problem_text}"
    if subtypes:
        return "、".join(subtypes[:3])
    if categories:
        return f"{'、'.join(categories[:2])}异常"
    return fallback


def _state_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Keep factual summaries for lifecycle comparison without raw evidence text."""

    allowed = {
        "first_seen_ts",
        "last_seen_ts",
        "time",
        "duration",
        "servers",
        "backends",
        "locations",
        "worlds",
        "positions",
        "where",
        "people",
        "people_text",
        "plugins",
        "components",
        "log_files",
        "configuration_files",
        "external_services",
        "categories",
        "subtypes",
        "impacts",
        "evidence_count",
    }
    return {key: value for key, value in facts.items() if key in allowed}


def _recovered_incident(state_key: str, prior: dict[str, Any]) -> dict[str, Any]:
    severity = str(prior.get("severity") or "low")
    return {
        "incident_id": str(prior.get("incident_id") or _incident_id(state_key)),
        "state_key": state_key,
        "title": str(prior.get("title") or "上一周期事件"),
        "family": str(prior.get("family") or "operations"),
        "severity": severity,
        "action_label": "无需处理",
        "impact_label": "当前没有再次出现",
        "lifecycle": "recovered",
        "lifecycle_label": "已恢复",
        "needs_review": bool(prior.get("needs_review")),
        "facts": prior.get("facts") or {},
        "impact": str(prior.get("impact") or "上一周期影响范围"),
        "summary": "本周期未再次观察到同类信号，暂按已恢复记录。",
        "assessment": "当前仅能确认监控窗口内未复现；仍需按完成标准观察。",
        "ai_reviewed": bool(prior.get("ai_reviewed")),
        "ai_plan_used": bool(prior.get("ai_plan_used")),
        "check_plan_source": str(prior.get("check_plan_source") or "rules"),
        "action_now": "无需继续升级，保留证据并进入恢复观察。",
        "action_next": "在下一巡检窗口确认同类信号仍未出现。",
        "check_plan": prior.get("check_plan") or [],
        "verification": str(prior.get("verification") or "连续一个巡检窗口未再出现同类信号"),
        "owner_role": str(prior.get("owner_role") or "值班管理员"),
        "evidence_count": _positive_int(prior.get("evidence_count"), 0),
        "evidence": [],
        "time_range": "上一巡检周期",
        "first_seen_ts": _positive_int(prior.get("first_seen_ts"), 0),
        "last_seen_ts": _positive_int(prior.get("last_seen_ts"), 0),
        "research_sources": "",
    }


def _incident_signature(group: IncidentGroup) -> str:
    tags = sorted(
        {
            str(issue.get("tag") or issue.get("category") or "unknown").lower()
            for issue in group.issues
        }
    )
    semantics = sorted(
        {
            str(value).strip().lower()
            for issue in group.issues
            for field in ("ops_subtypes", "chat_labels")
            for value in (issue.get(field) or [])
            if str(value).strip()
        }
    )
    raw = "|".join(
        [
            group.family,
            ",".join(sorted(group.scopes)),
            ",".join(tags),
            ",".join(semantics),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _incident_id(state_key: str) -> str:
    """Build a stable ID that remains unique for repeated same-signature events."""

    digest = hashlib.sha256(state_key.encode("utf-8")).hexdigest()[:12].upper()
    return f"MS-{digest}"


def _lifecycle_for(group: IncidentGroup, prior: dict[str, Any] | None) -> str:
    if not prior:
        return "new"
    previous_last = _positive_int(prior.get("last_seen_ts"), 0)
    current_first = int(group.start_ts or group.end_ts or 0)
    if previous_last and current_first and current_first <= previous_last + CONTINUITY_GAP_MS:
        return "ongoing"
    return "new"


def _action_queue(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for incident in incidents:
        if incident.get("lifecycle") == "recovered":
            continue
        severity = str(incident.get("severity") or "low")
        queue.append(
            {
                "incident_id": incident["incident_id"],
                "severity": severity,
                "action_label": action_label(severity),
                "timing": action_timing(severity),
                "action": incident["action_now"],
                "owner_role": incident["owner_role"],
                "verification": incident["verification"],
                "time": (incident.get("facts") or {}).get("time"),
                "where": (incident.get("facts") or {}).get("where"),
                "people": (incident.get("facts") or {}).get("people_text"),
                "components": (incident.get("facts") or {}).get("components"),
                "check_plan": incident.get("check_plan") or [],
            }
        )
    return queue


def _lifecycle_counts(incidents: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": len(incidents),
        "new": 0,
        "ongoing": 0,
        "recovered": 0,
        "needs_review": 0,
        "immediate_action": 0,
    }
    for incident in incidents:
        lifecycle = str(incident.get("lifecycle") or "")
        if lifecycle in counts:
            counts[lifecycle] += 1
        if incident.get("needs_review"):
            counts["needs_review"] += 1
        if (
            str(incident.get("severity") or "low") in {"critical", "high"}
            and lifecycle != "recovered"
        ):
            counts["immediate_action"] += 1
    return counts


def _management_status(incidents: list[dict[str, Any]]) -> tuple[str, str]:
    active = [item for item in incidents if item.get("lifecycle") != "recovered"]
    if any(
        str(item.get("severity") or "low") in {"critical", "high"}
        for item in active
    ):
        return "需要立即处置", "critical"
    if active:
        return "需要关注", "attention"
    if any(item.get("lifecycle") == "recovered" for item in incidents):
        return "已恢复，继续观察", "recovered"
    return "运行稳定", "stable"


def _management_summary(
    report_type: str,
    incidents: list[dict[str, Any]],
    counts: dict[str, int],
    observation_count: int,
) -> str:
    active = [item for item in incidents if item.get("lifecycle") != "recovered"]
    if report_type == "postmortem":
        if not incidents:
            return "复盘窗口内未识别到可形成事件的异常；请确认时间范围和证据源是否完整。"
        top = incidents[0]
        facts = top.get("facts") or {}
        return (
            f"本次复盘覆盖 {len(incidents)} 个事件，"
            f"其中 {counts.get('immediate_action', 0)} 个需要马上处理。"
            f"首要事件发生于 {facts.get('time') or top.get('time_range')}，地点 {facts.get('where') or '未记录'}，"
            f"涉及 {facts.get('components') or '未识别组件'}，人物 {facts.get('people_text') or '未关联具体玩家'}；"
            f"影响判断：{top.get('impact_label') or '影响尚不明确'}。"
            f"处理建议：{top.get('action_now')}"
        )
    if not active:
        if counts.get("recovered"):
            return (
                f"上一周期的 {counts['recovered']} 个事件本窗口未再复现，"
                f"另有 {observation_count} 项一般观察；建议完成恢复确认后关闭事件。"
            )
        return f"本窗口未发现需要升级处置的事件，记录 {observation_count} 项一般观察。"
    top = active[0]
    facts = top.get("facts") or {}
    return (
        f"当前有 {len(active)} 个还没处理完的事件，"
        f"其中 {counts.get('immediate_action', 0)} 个需要马上处理。"
        f"首要事件“{top.get('title')}”发生于 {facts.get('time') or top.get('time_range')}，"
        f"地点 {facts.get('where') or '未记录'}，插件/组件 {facts.get('components') or '未识别'}，"
        f"人物 {facts.get('people_text') or '未关联具体玩家'}。"
        f"首要操作：{top.get('action_now')}"
    )


def _observation_summary(report: dict[str, Any], count: int) -> str:
    if count <= 0:
        return "本窗口无需要单独记录的一般观察。"
    chat = str(report.get("chat_summary") or "").strip()
    if chat:
        return chat
    return f"本窗口另记录 {count} 项低风险运行或社区观察，未升级为处置事件。"


def _next_step_for(group: IncidentGroup) -> str:
    if group.family == "operations":
        return "保留变更与日志证据；若信号持续，升级给对应服务或插件负责人。"
    if group.family in {"moderation", "community", "chat_review"}:
        return "记录人工复核结论；证据不足时保持观察，不执行不可逆处罚。"
    if group.family == "player_feedback":
        return "关联玩家反馈与服务端日志，无法当班解决时转为可跟踪事项。"
    return "在下一巡检窗口复核，并记录是否关闭或升级。"


def _owner_for(group: IncidentGroup) -> str:
    if group.family == "operations":
        return "服务器运维"
    if group.family in {"moderation", "community", "chat_review"}:
        return "社区管理员"
    if group.family == "player_feedback":
        return "值班客服/管理员"
    return "值班管理员"


def _next_update(actions: list[dict[str, Any]], report_type: str) -> str:
    if report_type == "postmortem":
        return "行动项状态发生变化时"
    if any(
        str(action.get("severity") or "low") in {"critical", "high"}
        for action in actions
    ):
        return "30 分钟内或状态变化时"
    if actions:
        return "下一班次或下一巡检窗口"
    return "按既定巡检周期"


def _format_servers(report: dict[str, Any]) -> str:
    values: list[str] = []
    fields = ("server_names",) if report.get("server_names") else ("servers",)
    for field in fields + ("proxy_ids",):
        raw = report.get(field) or []
        if isinstance(raw, str):
            raw = [raw]
        for value in raw:
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
    return " / ".join(values) if values else "全部服务器"


def _report_end(report: dict[str, Any]) -> int:
    value = report.get("window_end_ts")
    if not value and isinstance(report.get("time_window"), dict):
        value = report["time_window"].get("end")
    return _positive_int(value, 0)


def _managed_incident_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    recovered = 1 if item.get("lifecycle") == "recovered" else 0
    severity = SEVERITY_RANK.get(str(item.get("severity") or "low"), 0)
    return recovered, -severity, _positive_int(item.get("first_seen_ts"), 2**63 - 1)


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default
