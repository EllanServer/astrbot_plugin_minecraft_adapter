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
    record_location,
)
from .dialogue import PlayerDialogueAnalyzer
from .metrics_context import (
    MetricContextAccumulator,
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

CATEGORY_TERMS = {
    category: tuple(keyword.lower() for keyword in keywords)
    for category, keywords in CATEGORY_KEYS.items()
}
WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+")
_MISSING_DIALOGUE_RULE = object()


class _GroupSummaryAccumulator:
    """Incrementally collect bucket summary fields while bucketing records."""

    def __init__(self):
        self.identities: set[str] = set()
        self.player_names: list[str] = []
        self._seen_player_names: set[str] = set()
        self.servers: set[str] = set()
        self.backends: set[str] = set()
        self.locations: list[str] = []
        self._seen_locations: set[str] = set()

    def add(self, record: ObservationRecord):
        if record.identity:
            self.identities.add(record.identity)
        name = (record.player_name or record.identity or "").strip()
        if name and name not in self._seen_player_names:
            self._seen_player_names.add(name)
            self.player_names.append(name)
        if record.server_id:
            self.servers.add(record.server_id)
        if record.backend_server:
            self.backends.add(record.backend_server)
        location = record_location(record)
        if location and location not in self._seen_locations:
            self._seen_locations.add(location)
            self.locations.append(location)

    def summary(self) -> dict[str, list[str]]:
        return {
            "identities": sorted(self.identities),
            "player_names": sorted(self.player_names),
            "servers": sorted(self.servers),
            "backends": sorted(self.backends),
            "locations": sorted(self.locations),
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
        servers: set[str] = set()
        server_names: set[str] = set()
        proxy_ids: set[str] = set()
        chat_records: list[ObservationRecord] = []
        metrics_count = 0
        plugin_error_count = 0
        metrics_accumulator = MetricContextAccumulator()
        categories: dict[str, list[str]] = {key: [] for key in CATEGORY_KEYS}
        buckets: dict[tuple[str, str], list[ObservationRecord]] = defaultdict(list)
        bucket_summary_accumulators: dict[tuple[str, str], _GroupSummaryAccumulator] = {}
        dialogue, dialogue_rule_cache = self.dialogue.analyze_with_classifications(records)
        use_dialogue_cache = self.config.dialogue.enabled

        for record in records:
            if record.server_id:
                servers.add(record.server_id)
            server_name = record.server_name or record.server_id
            if server_name:
                server_names.add(server_name)
            if record.proxy_id:
                proxy_ids.add(record.proxy_id)
            if record.kind == "CHAT":
                chat_records.append(record)
            elif record.kind == "SERVER_METRICS":
                metrics_count += 1
                metrics_accumulator.add(record)
            elif record.kind == "PLUGIN_ERROR":
                plugin_error_count += 1

            if use_dialogue_cache:
                category, tag = self.classify_and_tag(
                    record,
                    dialogue_rule_cache.get(id(record)),
                )
            else:
                category, tag = self.classify_and_tag(record)
            bucket_key = (category, tag)
            buckets[bucket_key].append(record)
            bucket_summary_accumulators.setdefault(
                bucket_key,
                _GroupSummaryAccumulator(),
            ).add(record)

        bucket_summaries = {
            key: accumulator.summary()
            for key, accumulator in bucket_summary_accumulators.items()
        }
        for (category, tag), group in buckets.items():
            categories.setdefault(category, [])
            categories[category].append(
                self._category_line(tag, group, bucket_summaries[(category, tag)])
            )

        issues = []
        issues.extend(dialogue.get("issues") or [])
        dialogue_issue_keys = self._dialogue_issue_keys(issues)
        for (category, tag), group in sorted(
            buckets.items(), key=lambda item: len(item[1]), reverse=True
        ):
            if (category, tag) in dialogue_issue_keys:
                continue
            if category == "daily" and len(group) < 3:
                continue
            summary = bucket_summaries[(category, tag)]
            players = summary["identities"]
            player_names = summary["player_names"]
            affected = summary["servers"]
            backends = summary["backends"]
            locations = summary["locations"]
            severity = self._severity(category, group, players, affected)
            samples = self._evidence_samples(group)
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
                    "suggested_action": self._suggest_action(category, severity, tag),
                    "should_alert": self._should_alert(
                        severity, len(group), len(players)
                    ),
                }
            )

        chat_players = player_name_list(chat_records)
        metrics_by_location = metrics_accumulator.build()
        issues = enrich_issues_with_metrics(issues, metrics_by_location)
        for category, lines in (dialogue.get("category_lines") or {}).items():
            categories.setdefault(category, [])
            categories[category] = list(lines) + categories.get(category, [])
        if not categories["daily"]:
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
            "servers": sorted(servers) if not server_id else [server_id],
            "server_names": sorted(server_names),
            "proxy_ids": sorted(proxy_ids),
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
            "ops_notes": self._ops_notes(
                records,
                metrics_by_location,
                plugin_error_count,
            ),
        }

    def classify(self, record: ObservationRecord) -> str:
        return self.classify_and_tag(record)[0]

    def tag(self, record: ObservationRecord) -> str:
        return self.classify_and_tag(record)[1]

    def classify_and_tag(
        self,
        record: ObservationRecord,
        dialogue_rule: Any = _MISSING_DIALOGUE_RULE,
    ) -> tuple[str, str]:
        issue_tag = _structured_issue_tag(record)
        if issue_tag:
            return issue_tag
        if record.kind == "SERVER_SWITCH":
            return "cross_server", "server_switch"
        if record.kind == "PLUGIN_ERROR":
            return "bug", self._content_tag(record)
        if record.kind in ("PLAYER_JOIN", "PLAYER_QUIT", "SERVER_METRICS"):
            tag = "server_metrics" if record.kind == "SERVER_METRICS" else record.kind.lower()
            return "daily", tag
        if dialogue_rule is _MISSING_DIALOGUE_RULE:
            dialogue_rule = self.dialogue.matched_rule(record)
        if dialogue_rule:
            return dialogue_rule.category, f"dialogue:{dialogue_rule.tag}"
        text = f"{record.content} {' '.join(record.tags)}".lower()
        for category, keywords in CATEGORY_TERMS.items():
            if any(keyword in text for keyword in keywords):
                return category, self._content_tag(record)
        return "daily", self._content_tag(record)

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

    @staticmethod
    def _dialogue_issue_keys(issues: list[dict[str, Any]]) -> set[tuple[str, str]]:
        return {
            (str(issue.get("category") or ""), str(issue.get("source_tag") or ""))
            for issue in issues
            if str(issue.get("source_tag") or "").startswith("dialogue:")
        }

    def _content_tag(self, record: ObservationRecord) -> str:
        words = WORD_RE.findall(record.content.lower())
        keywords = [word for word in words if len(word) >= 2][:3]
        return "/".join(keywords) if keywords else record.kind.lower()

    def _category_line(
        self,
        tag: str,
        group: list[ObservationRecord],
        summary: dict[str, list[str]] | None = None,
    ) -> str:
        summary = summary or self._group_summary(group)
        players = len(summary["identities"])
        player_names = summary["player_names"]
        servers = ", ".join(summary["servers"])
        return (
            f"{tag}: {len(group)} 条观察，涉及 {players} 名玩家"
            f"（{format_players(player_names)}），服务器 {servers or '未知'}。"
        )

    @staticmethod
    def _group_summary(group: list[ObservationRecord]) -> dict[str, list[str]]:
        return {
            "identities": sorted({record.identity for record in group if record.identity}),
            "player_names": player_name_list(group),
            "servers": sorted({record.server_id for record in group if record.server_id}),
            "backends": sorted(
                {record.backend_server for record in group if record.backend_server}
            ),
            "locations": location_list(group),
        }

    def _severity(
        self,
        category: str,
        group: list[ObservationRecord],
        players: list[str],
        affected: list[str],
    ) -> str:
        if category == "bug" and (len(players) >= 3 or len(group) >= 8):
            return "critical"
        if category == "bug" and any(
            record.kind in ("PLUGIN_ERROR", "SERVER_STARTUP") for record in group
        ):
            if len(group) >= 5:
                return "high"
            return "medium"
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

    def _suggest_action(self, category: str, severity: str, tag: str = "") -> str:
        if tag == "server_security_warning":
            return "请检查服务端运行用户、online-mode/Velocity 转发安全与防火墙限制，避免外部玩家伪造身份进入。"
        if tag == "slow_startup":
            return "请优先查看启动阶段耗时插件、资源包/模型加载和数据库连接，必要时拆分启动耗时。"
        if tag == "database_warning":
            return "请核对 MariaDB/Flyway 兼容性与数据库连接配置，确认是否影响聊天、经济或同步类插件。"
        if tag == "mythicmobs_config_error":
            return "请检查 MythicMobs 技能/怪物配置中无效粒子、召唤类型和版本兼容项。"
        if tag == "data_converter_error":
            return "请定位触发 JSON/NBT 转换失败的资源或家具配置，修正后重载前先备份相关数据。"
        if tag == "plugin_config_error":
            return "请检查对应插件配置文件格式、路径和权限，优先修复启动时无法加载的配置。"
        if tag == "plugin_integration_warning":
            return "请检查第三方 API Key、网络连接或可选集成配置，确认是否只是功能降级。"
        if category == "bug":
            return "请管理员查看相关插件日志和玩家证据，确认后再人工处理。"
        if category == "economy":
            return "请核对经济/玩法数据来源，确认异常范围后再决定是否回滚或修正。"
        if category == "moderation":
            return "请人工复核聊天与行为证据，不要仅凭单条观察处罚玩家。"
        if severity in ("high", "critical"):
            return "建议尽快人工确认，并在群内同步处理状态。"
        return "持续观察即可。"

    def _evidence_samples(self, group: list[ObservationRecord]) -> list[str]:
        if not self.config.report.include_evidence_samples:
            return []
        return [
            item.evidence_text()
            for item in group[: self.config.report.max_evidence_samples]
        ]

    def _ops_notes(
        self,
        records: list[ObservationRecord],
        metrics_by_location: dict[str, dict[str, Any]] | None = None,
        plugin_error_count: int | None = None,
    ) -> list[str]:
        if metrics_by_location is None:
            metrics_by_location = build_metric_context(records)
        notes = metric_ops_notes(metrics_by_location)
        if plugin_error_count is None:
            plugin_error_count = sum(
                1 for record in records if record.kind == "PLUGIN_ERROR"
            )
        if plugin_error_count:
            notes.append(f"检测到 {plugin_error_count} 条插件错误观察。")
        command_count = sum(1 for record in records if record.kind == "ADMIN_COMMAND")
        if command_count:
            notes.append(f"检测到 {command_count} 条管理员命令记录，建议结合时间线核对变更影响。")
        return notes


def _structured_issue_tag(record: ObservationRecord) -> tuple[str, str] | None:
    for tag in record.tags or []:
        raw = str(tag)
        if not raw.startswith("issue:"):
            continue
        parts = raw.split(":", 2)
        if len(parts) != 3:
            continue
        category = parts[1].strip()
        issue_tag = parts[2].strip()
        if category in CATEGORY_KEYS and issue_tag:
            return category, issue_tag
    return None
