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
            location_line = (
                f"位置：{location}\n"
                if location and location != "未知"
                else ""
            )
            incident = format_issue_incident(issue)
            incident_line = f"{incident}\n" if incident else ""
            time_range = format_issue_time_range(issue)
            time_line = f"时间：{time_range}\n" if time_range else ""
            terms = format_issue_terms(issue)
            terms_line = f"关键词：{terms}\n" if terms else ""
            messages.append(
                "MineSentinel 即时告警\n"
                f"服务器：{server_id}\n"
                f"级别：{issue.get('severity')}\n"
                f"问题：{issue.get('tag')}\n"
                f"{incident_line}"
                f"{time_line}"
                f"证据：{issue.get('evidence_count')} 条\n"
                f"{signal_line}"
                f"{location_line}"
                f"{terms_line}"
                f"建议：{issue.get('suggested_action')}"
            )
        return messages
