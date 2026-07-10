"""MineSentinel alert decision and formatting."""

from __future__ import annotations

import time
from typing import Any

from .issue_formatting import (
    format_issue_incident,
    format_issue_terms,
    format_issue_time_range,
)
from .models import MineSentinelConfig
from .reporting.incident_response import (
    build_check_plan,
    build_incident_facts,
    build_reader_action,
    build_reader_verification,
    format_check_step,
    infer_family,
)
from .reporting.labels import (
    DEFAULT_LABELS,
    action_label,
    action_timing,
    impact_label,
)


class MineSentinelAlertEngine:
    def __init__(self, config: MineSentinelConfig):
        self.config = config
        self._alert_cooldowns: dict[str, float] = {}
        self._last_analysis: dict[str, float] = {}

    def _cleanup_dicts(self, now: float) -> None:
        """清理过期的 cooldown/analysis 条目，避免字典无界增长。

        仅保留最近 2 倍间隔内的条目，已过期 2 倍以上的视为不再活跃可安全移除。
        """
        analysis_ttl = self.config.alert.analysis_interval_seconds * 2
        cooldown_ttl = self.config.alert.cooldown_seconds * 2
        self._last_analysis = {
            sid: ts
            for sid, ts in self._last_analysis.items()
            if now - ts <= analysis_ttl
        }
        self._alert_cooldowns = {
            key: ts
            for key, ts in self._alert_cooldowns.items()
            if now - ts <= cooldown_ttl
        }

    def should_analyze(self, server_id: str) -> bool:
        if not self.config.alert.enabled:
            return False
        now = time.time()
        if (
            now - self._last_analysis.get(server_id, 0)
            < self.config.alert.analysis_interval_seconds
        ):
            return False
        self._cleanup_dicts(now)
        self._last_analysis[server_id] = now
        return True

    def build_messages(self, server_id: str, report: dict[str, Any]) -> list[str]:
        messages = []
        # should_analyze 已做过一次清理；此处进入 build_messages 时再清理一次，
        # 后续循环内不再重复调用，避免每个 issue 都遍历字典。
        self._cleanup_dicts(time.time())
        for issue in report.get("issues", []):
            if not issue.get("should_alert"):
                continue
            key = f"{server_id}:{issue.get('category')}:{issue.get('tag')}"
            now = time.time()
            if (
                now - self._alert_cooldowns.get(key, 0)
                < self.config.alert.cooldown_seconds
            ):
                continue
            self._alert_cooldowns[key] = now
            signal_count = issue.get("signal_count")
            evidence_count = issue.get("evidence_count")
            signal_line = (
                f"去重信号：{signal_count} 个\n"
                if signal_count and evidence_count and signal_count != evidence_count
                else ""
            )
            location = issue.get("affected_locations_text") or ""
            incident = format_issue_incident(issue)
            incident_line = f"{incident}\n" if incident else ""
            time_range = format_issue_time_range(issue)
            time_line = f"时间：{time_range}\n" if time_range else ""
            terms = format_issue_terms(issue)
            terms_line = f"关键词：{terms}\n" if terms else ""
            severity = str(issue.get("severity") or "high").lower()
            handling = action_label(severity)
            timing = action_timing(severity)
            family = infer_family(issue)
            facts = build_incident_facts(
                [issue],
                fallback_time_range=time_range,
            )
            check_plan = build_check_plan([issue], facts, family)
            action = build_reader_action([issue], facts, family)
            verification = build_reader_verification([issue], facts, family)
            check_lines = "\n".join(
                f"{index}. {format_check_step(step)}"
                for index, step in enumerate(check_plan[:3], 1)
            )
            messages.append(
                f"MineSentinel 即时事件 · {handling}\n"
                "状态：新发，等待确认\n"
                f"事件：{DEFAULT_LABELS.issue_title(issue)}\n"
                f"时间：{facts.get('time') or time_range or '未记录'}\n"
                f"地点：{facts.get('where') or server_id}\n"
                f"人物：{facts.get('people_text') or '未关联具体玩家'}\n"
                f"插件/组件：{facts.get('components') or '未识别'}\n"
                f"日志文件：{'、'.join(facts.get('log_files') or []) or '未记录'}\n"
                f"处理要求：{handling}（{timing}）\n"
                f"现在要做：{action}\n"
                f"负责人：{_owner(issue)}\n"
                f"完成标准：{verification}\n"
                "下一次更新：30 分钟内或状态变化时\n"
                "\n"
                f"服务器：{server_id}\n"
                f"影响范围：{location if location and location != '未知' else facts.get('where') or server_id}\n"
                f"影响判断：{impact_label(severity)}\n"
                f"{incident_line}"
                f"{time_line}"
                f"相关日志：{issue.get('evidence_count')} 条\n"
                f"{signal_line}"
                f"{terms_line}"
                "处理办法：\n"
                f"{check_lines}\n"
                "判断依据：MineSentinel 已分析相关日志；完整原文随巡检报告发送。"
            )
        return messages


def _owner(issue: dict[str, Any]) -> str:
    category = str(issue.get("category") or "").lower()
    if category in {"community", "chat_review", "moderation"}:
        return "社区管理员"
    if category in {"player_feedback", "complaint"}:
        return "值班客服/管理员"
    return "服务器运维"
