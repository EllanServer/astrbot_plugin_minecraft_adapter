"""Rule-based MineSentinel report analysis."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .common import (
    SEVERITY_RANK,
    format_locations,
    format_players,
    location_list,
    player_name_list,
)
from .dialogue import PlayerDialogueAnalyzer
from .metrics_context import (
    build_metric_context,
    enrich_issues_with_metrics,
    metric_ops_notes,
)


CATEGORY_KEYS = {
    "daily": ["上线", "下线", "join", "quit", "hello", "hi"],
    "complaint": ["卡", "不满", "炸了", "延迟", "掉线", "lag", "rollback"],
    "bug": ["bug", "报错", "没了", "丢失", "坏了", "异常", "error", "exception"],
    "economy": ["钱", "经济", "商店", "金币", "价格", "刷物品", "复制", "market"],
    "moderation": ["外挂", "作弊", "骂", "封", "举报", "ban", "cheat"],
    "suggestion": ["建议", "希望", "能不能", "加个", "优化", "suggest"],
    "cross_server": ["跨服", "切服", "传送", "lobby", "server_switch"],
}

class HeuristicReportBuilder:
    """Builds the deterministic report used as fallback and AI grounding."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config
        self.dialogue = PlayerDialogueAnalyzer(config)

    def build(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
    ) -> dict[str, Any]:
        servers = sorted({record.server_id for record in records if record.server_id})
        server_names = sorted(
            {
                record.server_name or record.server_id
                for record in records
                if record.server_name or record.server_id
            }
        )
        proxy_ids = sorted({record.proxy_id for record in records if record.proxy_id})
        categories: dict[str, list[str]] = {key: [] for key in CATEGORY_KEYS}
        buckets: dict[tuple[str, str], list[ObservationRecord]] = defaultdict(list)
        dialogue = self.dialogue.analyze(records)

        for record in records:
            category = self.classify(record)
            tag = self.tag(record)
            buckets[(category, tag)].append(record)

        for (category, tag), group in buckets.items():
            categories.setdefault(category, [])
            categories[category].append(self._category_line(tag, group))

        issues = []
        issues.extend(dialogue.get("issues") or [])
        for (category, tag), group in sorted(
            buckets.items(), key=lambda item: len(item[1]), reverse=True
        ):
            if self._has_dialogue_issue(issues, category, tag):
                continue
            if category == "daily" and len(group) < 3:
                continue
            players = sorted({record.identity for record in group if record.identity})
            player_names = player_name_list(group)
            affected = sorted({record.server_id for record in group if record.server_id})
            backends = sorted(
                {record.backend_server for record in group if record.backend_server}
            )
            locations = location_list(group)
            severity = self._severity(category, group, players, affected)
            samples = [
                item.evidence_text()
                for item in group[: self.config.report.max_evidence_samples]
            ]
            issues.append(
                {
                    "category": category,
                    "tag": tag,
                    "severity": severity,
                    "confidence": min(
                        0.95, 0.45 + len(group) * 0.08 + len(players) * 0.05
                    ),
                    "affected_servers": affected,
                    "affected_backends": backends,
                    "affected_locations": locations,
                    "affected_locations_text": format_locations(locations),
                    "evidence_count": len(group),
                    "unique_players": len(players),
                    "players": player_names,
                    "players_text": format_players(player_names),
                    "evidence_samples": (
                        samples if self.config.report.include_evidence_samples else []
                    ),
                    "suggested_action": self._suggest_action(category, severity),
                    "should_alert": self._should_alert(
                        severity, len(group), len(players)
                    ),
                }
            )

        chat_records = [record for record in records if record.kind == "CHAT"]
        chat_players = player_name_list(chat_records)
        metrics_by_location = build_metric_context(records)
        issues = enrich_issues_with_metrics(issues, metrics_by_location)
        for category, lines in (dialogue.get("category_lines") or {}).items():
            categories.setdefault(category, [])
            categories[category] = list(lines) + categories.get(category, [])
        if not categories["daily"]:
            metrics_count = sum(
                1 for record in records if record.kind == "SERVER_METRICS"
            )
            categories["daily"].append(
                f"窗口内收到 {len(records)} 条观察，聊天 {len(chat_records)} 条，"
                f"指标 {metrics_count} 条。"
            )

        return {
            "summary": (
                f"最近 {window_minutes} 分钟收到 {len(records)} 条 MineSentinel 观察，"
                f"其中聊天 {len(chat_records)} 条，发言玩家：{format_players(chat_players)}。"
            ),
            "time_window": f"最近 {window_minutes} 分钟",
            "servers": servers if not server_id else [server_id],
            "server_names": server_names,
            "proxy_ids": proxy_ids,
            "chat_count": len(chat_records),
            "chat_players": chat_players,
            "chat_players_text": format_players(chat_players),
            "dialogue_findings": dialogue.get("findings") or [],
            "categories": {
                "daily": categories.get("daily", []),
                "complaint": categories.get("complaint", []),
                "bug": categories.get("bug", []),
                "economy": categories.get("economy", []),
                "moderation": categories.get("moderation", []),
                "suggestion": categories.get("suggestion", []),
                "cross_server": categories.get("cross_server", []),
            },
            "issues": issues,
            "ops_notes": self._ops_notes(records, metrics_by_location),
        }

    def classify(self, record: ObservationRecord) -> str:
        if record.kind == "SERVER_SWITCH":
            return "cross_server"
        if record.kind == "PLUGIN_ERROR":
            return "bug"
        if record.kind in ("PLAYER_JOIN", "PLAYER_QUIT", "SERVER_METRICS"):
            return "daily"
        dialogue_category = self.dialogue.classify_record(record)
        if dialogue_category:
            return dialogue_category
        text = f"{record.content} {' '.join(record.tags)}".lower()
        for category, keywords in CATEGORY_KEYS.items():
            if any(keyword.lower() in text for keyword in keywords):
                return category
        return "daily"

    @staticmethod
    def _has_dialogue_issue(
        issues: list[dict[str, Any]],
        category: str,
        tag: str,
    ) -> bool:
        return any(
            issue.get("category") == category
            and str(issue.get("source_tag") or "").startswith("dialogue:")
            and issue.get("source_tag") == tag
            for issue in issues
        )

    def tag(self, record: ObservationRecord) -> str:
        if record.kind == "SERVER_METRICS":
            return "server_metrics"
        if record.kind == "SERVER_SWITCH":
            return "server_switch"
        dialogue_rule = self.dialogue.matched_rule(record)
        if dialogue_rule:
            return f"dialogue:{dialogue_rule.tag}"
        words = re.findall(r"[\w\u4e00-\u9fff]+", record.content.lower())
        keywords = [word for word in words if len(word) >= 2][:3]
        return "/".join(keywords) if keywords else record.kind.lower()

    def _category_line(self, tag: str, group: list[ObservationRecord]) -> str:
        players = len({record.identity for record in group if record.identity})
        player_names = player_name_list(group)
        servers = ", ".join(sorted({record.server_id for record in group if record.server_id}))
        return (
            f"{tag}: {len(group)} 条观察，涉及 {players} 名玩家"
            f"（{format_players(player_names)}），服务器 {servers or '未知'}。"
        )

    def _severity(
        self,
        category: str,
        group: list[ObservationRecord],
        players: list[str],
        affected: list[str],
    ) -> str:
        if category == "bug" and (len(players) >= 3 or len(group) >= 8):
            return "critical"
        if len(affected) >= 2 and category in (
            "bug",
            "complaint",
            "economy",
            "cross_server",
        ):
            return "high"
        if len(players) >= 3 or len(group) >= 5:
            return "high"
        if len(group) >= 2:
            return "medium"
        return "low"

    def _should_alert(
        self, severity: str, evidence_count: int, unique_players: int
    ) -> bool:
        alert = self.config.alert
        return (
            alert.enabled
            and SEVERITY_RANK.get(severity, 0)
            >= SEVERITY_RANK.get(alert.min_severity, 3)
            and evidence_count >= alert.min_evidence_count
            and unique_players >= alert.min_unique_players
        )

    def _suggest_action(self, category: str, severity: str) -> str:
        if category == "bug":
            return "请管理员查看相关插件日志和玩家证据，确认后再人工处理。"
        if category == "economy":
            return "请核对经济/玩法数据来源，确认异常范围后再决定是否回滚或修正。"
        if category == "moderation":
            return "请人工复核聊天与行为证据，不要仅凭单条观察处罚玩家。"
        if severity in ("high", "critical"):
            return "建议尽快人工确认，并在群内同步处理状态。"
        return "持续观察即可。"

    def _ops_notes(
        self,
        records: list[ObservationRecord],
        metrics_by_location: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        notes = metric_ops_notes(metrics_by_location or build_metric_context(records))
        plugin_errors = [record for record in records if record.kind == "PLUGIN_ERROR"]
        if plugin_errors:
            notes.append(f"检测到 {len(plugin_errors)} 条插件错误观察。")
        return notes
