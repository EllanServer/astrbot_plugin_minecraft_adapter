"""Rule-based analysis for Minecraft runtime logs.

分类优先级（先命中先返回）：
    community > complaint > network > plugin > cross_server > moderation > bug > economy > daily

严重级别：
    critical: 崩溃/OOM/watchdog/服务停止/代理大面积不可用
    high:     循环刷屏、多条 ERROR、插件加载失败、多服务器受影响、性能问题重复
    medium:   单条 ERROR、多条 WARN、单次性能警告、权限/登录/网络异常
    low:      单条 WARN、日常 join/quit/start/stop、无明显异常的普通日志

告警策略：
    critical 直告；high 默认 evidence_count >= min_evidence_count；
    medium 仅在多服务器/多后端或证据数较多时告警；low 不告警。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .common import SEVERITY_RANK, format_locations, location_list


# --- 分类关键词表 ---------------------------------------------------------
CATEGORY_KEYS = {
    "daily": (
        "info",
        "started",
        "stopped",
        "done",
        "join",
        "joined",
        "quit",
        "left the game",
        "lost connection",
        "connected",
        "disconnected",
    ),
    "complaint": (
        "can't keep up",
        "overloaded",
        "lag",
        "lagging",
        "timeout",
        "timed out",
        "tps",
        "mspt",
        "server is overloaded",
        "moved too quickly",
        "moved wrongly",
        "卡顿",
        "延迟",
        "掉线",
        "超时",
        "服务器卡",
    ),
    "bug": (
        "error",
        "exception",
        "failed",
        "failure",
        "fatal",
        "severe",
        "crash",
        "warn",
        "warning",
        "stacktrace",
        "traceback",
        "nullpointerexception",
        "illegalargumentexception",
        "classnotfoundexception",
        "nosuchmethoderror",
        "unsupportedoperationexception",
        "cannot invoke",
        "报错",
        "异常",
        "失败",
        "警告",
        "崩溃",
    ),
    "economy": (
        "economy",
        "vault",
        "shop",
        "money",
        "coin",
        "balance",
        "pay",
        "sell",
        "buy",
        "auction",
        "market",
        "trade",
        "商店",
        "经济",
        "金币",
        "余额",
        "交易",
        "拍卖",
    ),
    "community": (
        "ban",
        "banned",
        "kick",
        "kicked",
        "mute",
        "muted",
        "report",
        "reported",
        "spam",
        "profanity",
        "grief",
        "griefing",
        "cheat",
        "cheating",
        "anticheat",
        "anti-cheat",
        "xray",
        "violation",
        "vl",
        "kill aura",
        "killaura",
        "封禁",
        "禁言",
        "踢出",
        "作弊",
        "外挂",
        "举报",
        "刷屏",
        "破坏",
    ),
    "moderation": (
        "whitelist",
        "permission",
        "permissions",
        "auth",
        "login",
        "logged in",
        "logged out",
        "premium",
        "offline mode",
        "online mode",
        "uuid",
        "session",
        "白名单",
        "权限",
        "登录",
        "认证",
        "正版验证",
    ),
    "suggestion": (),
    "cross_server": (
        "velocity",
        "bungeecord",
        "bungee",
        "proxy",
        "backend",
        "server switch",
        "forwarding",
        "modern forwarding",
        "player forwarding",
        "ip forwarding",
        "connection request",
        "转发",
        "后端",
        "代理",
        "跨服",
    ),
    "network": (
        "connection reset",
        "connection refused",
        "connection timed out",
        "read timed out",
        "broken pipe",
        "socket",
        "netty",
        "io.netty",
        "disconnect",
        "disconnected",
        "lost connection",
        "网络",
        "连接失败",
        "连接超时",
        "断开连接",
    ),
    "plugin": (
        "plugin",
        "plugins",
        "enabled plugin",
        "disabling plugin",
        "could not load",
        "could not enable",
        "depend",
        "dependency",
        "softdepend",
        "插件",
        "依赖",
        "加载失败",
        "启用失败",
    ),
}

# 分类匹配优先级（先匹配先返回）。daily 永远兜底。
CLASSIFY_PRIORITY = (
    "community",
    "complaint",
    "network",
    "plugin",
    "cross_server",
    "moderation",
    "bug",
    "economy",
    "daily",
)

# --- Marker 常量 ---------------------------------------------------------
ERROR_MARKERS = (
    "error",
    "exception",
    "failed",
    "failure",
    "fatal",
    "severe",
    "crash",
    "报错",
    "异常",
    "失败",
)
COMMUNITY_MARKERS = CATEGORY_KEYS["community"]
WARN_MARKERS = ("warn", "warning", "警告")
PERFORMANCE_MARKERS = (
    "can't keep up",
    "overloaded",
    "lag",
    "timeout",
    "timed out",
    "tps",
    "mspt",
    "卡顿",
    "延迟",
    "超时",
)
NETWORK_MARKERS = CATEGORY_KEYS["network"]
PLUGIN_MARKERS = CATEGORY_KEYS["plugin"]
CRITICAL_MARKERS = (
    "fatal",
    "severe",
    "crash",
    "outofmemoryerror",
    "out of memory",
    "watchdog",
    "server stopped",
    "tick took",
    "can't keep up! is the server overloaded",
    "崩溃",
    "内存溢出",
)
# community 里的反作弊信号词，命中即提升 community（避免 fly/speed 误伤）
ANTICHEAT_MARKERS = (
    "anticheat",
    "anti-cheat",
    "violation",
    "vl",
    "kill aura",
    "killaura",
    "xray",
    "cheat",
    "cheating",
    "grief",
    "griefing",
    "作弊",
    "外挂",
    "破坏",
)


class HeuristicReportBuilder:
    """Build deterministic fallback facts from SERVER_LOG records."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config

    def build(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
    ) -> dict[str, Any]:
        log_records = [record for record in records if record.kind == "SERVER_LOG"]
        servers = sorted({record.server_id for record in log_records if record.server_id})
        server_names = sorted(
            {
                record.server_name or record.server_id
                for record in log_records
                if record.server_name or record.server_id
            }
        )
        proxy_ids = sorted({record.proxy_id for record in log_records if record.proxy_id})
        categories: dict[str, list[str]] = {key: [] for key in CATEGORY_KEYS}
        buckets: dict[tuple[str, str], list[ObservationRecord]] = defaultdict(list)

        for record in log_records:
            category = self.classify(record)
            tag = self.tag(record)
            buckets[(category, tag)].append(record)

        for (category, tag), group in buckets.items():
            categories.setdefault(category, [])
            categories[category].append(self._category_line(tag, group))

        issues = []
        max_severity_rank = 0
        for (category, tag), group in sorted(
            buckets.items(), key=lambda item: len(item[1]), reverse=True
        ):
            severity = self._severity(group)
            severity_rank = SEVERITY_RANK.get(severity, 0)
            if severity_rank > max_severity_rank:
                max_severity_rank = severity_rank
            if category == "daily" and severity == "low":
                continue
            affected = sorted({record.server_id for record in group if record.server_id})
            backends = sorted(
                {record.backend_server for record in group if record.backend_server}
            )
            locations = location_list(group)
            samples = [
                item.evidence_text()
                for item in group[: self.config.report.max_evidence_samples]
            ]
            timestamps = [record.timestamp for record in group if record.timestamp]
            should_alert = self._should_alert(
                severity, len(group), affected, backends, category, group
            )
            issues.append(
                {
                    "category": category,
                    "tag": tag,
                    "severity": severity,
                    "confidence": min(0.98, 0.5 + len(group) * 0.08),
                    "affected_servers": affected,
                    "affected_backends": backends,
                    "affected_locations": locations,
                    "affected_locations_text": format_locations(locations),
                    "evidence_count": len(group),
                    "unique_players": 0,
                    "players": [],
                    "players_text": "无",
                    "first_seen_ts": min(timestamps) if timestamps else 0,
                    "last_seen_ts": max(timestamps) if timestamps else 0,
                    "evidence_samples": (
                        samples if self.config.report.include_evidence_samples else []
                    ),
                    "signal_count": len(group),
                    "issue_terms": self._issue_terms(group),
                    "suggested_action": self._suggest_action(category, tag, severity),
                    "should_alert": should_alert,
                }
            )

        if not categories["daily"]:
            categories["daily"].append(
                f"窗口内收到 {len(log_records)} 条 Minecraft 运行日志观察。"
            )

        max_severity = next(
            (name for name, rank in SEVERITY_RANK.items() if rank == max_severity_rank),
            "low",
        ) if max_severity_rank else "low"
        any_alert = any(issue["should_alert"] for issue in issues)
        ops_notes, counters = self._ops_notes(log_records, issues, max_severity, any_alert)

        return {
            "summary": (
                f"最近 {window_minutes} 分钟收到 {len(log_records)} 条 "
                "Minecraft 运行日志观察。"
            ),
            "time_window": f"最近 {window_minutes} 分钟",
            "servers": servers if not server_id else [server_id],
            "server_names": server_names,
            "proxy_ids": proxy_ids,
            "log_count": len(log_records),
            "incident_findings": [],
            "categories": self._categories_dict(categories),
            "issues": issues,
            "ops_notes": ops_notes,
            "max_severity": max_severity,
            "any_alert": any_alert,
            "counters": counters,
        }

    # --- 分类 -------------------------------------------------------------
    def classify(self, record: ObservationRecord) -> str:
        text = self._record_text(record)
        # community 优先：反作弊信号词命中即归 community
        if any(marker in text for marker in ANTICHEAT_MARKERS):
            return "community"
        # 其他 community 关键词（ban/kick/mute/spam 等）
        if any(marker in text for marker in COMMUNITY_MARKERS):
            return "community"
        if any(marker in text for marker in PERFORMANCE_MARKERS):
            return "complaint"
        if any(marker in text for marker in NETWORK_MARKERS):
            return "network"
        if any(marker in text for marker in PLUGIN_MARKERS):
            return "plugin"
        if any(marker in text for marker in CATEGORY_KEYS["cross_server"]):
            return "cross_server"
        if any(marker in text for marker in CATEGORY_KEYS["moderation"]):
            return "moderation"
        if any(marker in text for marker in ERROR_MARKERS + WARN_MARKERS):
            return "bug"
        if any(marker in text for marker in CATEGORY_KEYS["economy"]):
            return "economy"
        return "daily"

    # --- Tag --------------------------------------------------------------
    def tag(self, record: ObservationRecord) -> str:
        text = self._record_text(record)
        level = str((record.context or {}).get("level") or "").lower()
        if "loop_suppressed" in record.tags:
            return f"server_log_loop_{level or 'warn'}"
        if any(marker in text for marker in ANTICHEAT_MARKERS):
            return "server_log_community"
        if any(marker in text for marker in COMMUNITY_MARKERS):
            return "server_log_community"
        if any(marker in text for marker in CATEGORY_KEYS["moderation"]):
            return "server_log_auth"
        if any(marker in text for marker in PERFORMANCE_MARKERS):
            return "server_log_performance"
        if any(marker in text for marker in NETWORK_MARKERS):
            return "server_log_network"
        if any(marker in text for marker in PLUGIN_MARKERS):
            return "server_log_plugin"
        if any(marker in text for marker in CATEGORY_KEYS["cross_server"]):
            return "server_log_cross_server"
        if any(marker in text for marker in CATEGORY_KEYS["economy"]):
            return "server_log_economy"
        return f"server_log_{level or 'info'}"

    def _category_line(self, tag: str, group: list[ObservationRecord]) -> str:
        servers = ", ".join(sorted({record.server_id for record in group if record.server_id}))
        levels = sorted(
            {
                str((record.context or {}).get("level") or "INFO").upper()
                for record in group
            }
        )
        return (
            f"{tag}: {len(group)} 条运行日志，级别 {', '.join(levels)}，"
            f"服务器 {servers or '未知'}。"
        )

    # --- 严重级别 ---------------------------------------------------------
    def _severity(self, group: list[ObservationRecord]) -> str:
        text = " ".join(self._record_text(record) for record in group)
        n = len(group)
        # critical: 崩溃 / OOM / watchdog / 服务停止 / 代理大面积不可用
        if any(marker in text for marker in CRITICAL_MARKERS):
            return "critical"
        # high: 循环刷屏
        if "loop_suppressed" in text:
            return "high"
        # high: 插件加载/启用失败
        if any(
            marker in text
            for marker in ("could not load", "could not enable", "加载失败", "启用失败")
        ):
            return "high" if n >= 1 else "medium"
        # high: 多条 ERROR
        if any(marker in text for marker in ERROR_MARKERS):
            return "high" if n >= 2 else "medium"
        # high: 性能问题重复出现 >=3
        if any(marker in text for marker in PERFORMANCE_MARKERS):
            if n >= 3:
                return "high"
            return "medium" if n >= 1 else "low"
        # high: 网络错误 >=5
        if any(marker in text for marker in NETWORK_MARKERS):
            if n >= 5:
                return "high"
            return "medium" if n >= 2 else "low"
        # medium: 多条 WARN
        if any(marker in text for marker in WARN_MARKERS):
            return "medium" if n >= 2 else "low"
        # medium: 权限/登录/网络异常
        if any(marker in text for marker in CATEGORY_KEYS["moderation"]):
            return "medium" if n >= 1 else "low"
        return "low"

    # --- 告警判定 ---------------------------------------------------------
    def _should_alert(
        self,
        severity: str,
        evidence_count: int,
        affected_servers: list[str],
        affected_backends: list[str],
        category: str,
        group: list[ObservationRecord],
    ) -> bool:
        alert = self.config.alert
        if not alert.enabled:
            return False
        # critical 直告，不受 evidence_count 限制
        if severity == "critical":
            return True
        # 循环刷屏 + high/critical 强制告警
        text = " ".join(self._record_text(record) for record in group)
        if "loop_suppressed" in text and severity in {"high", "critical"}:
            return True
        # 多服务器/多后端 + medium/high 强制告警
        multi_scope = len(affected_servers) >= 2 or len(affected_backends) >= 2
        if multi_scope and severity in {"medium", "high"}:
            return True
        # low 不告警
        if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(alert.min_severity, 3):
            return False
        # 标准：severity >= min_severity 且 evidence_count >= min_evidence_count
        return evidence_count >= alert.min_evidence_count

    # --- 推荐动作（按分类细化）--------------------------------------------
    def _suggest_action(self, category: str, tag: str, severity: str) -> str:
        if severity == "critical":
            return (
                "优先处理：检查 latest.log、崩溃报告（crash-reports/）、压缩历史日志、"
                "最近部署/重启/插件更新记录，并确认是否需要临时回滚。"
            )
        if tag.startswith("server_log_loop_"):
            return "优先查看首条样本对应的插件或服务端模块，避免重复报错继续刷屏。"
        if category == "complaint" or tag == "server_log_performance":
            return (
                "检查 TPS、MSPT、内存、实体数量、区块加载、红石机器、定时任务和插件耗时；"
                "优先对照 spark/timings 与 latest.log。"
            )
        if category == "network" or tag == "server_log_network":
            return (
                "检查代理到后端的连通性、端口、防火墙、Velocity/Bungee 转发配置、"
                "后端在线状态，以及玩家来源网络是否集中异常。"
            )
        if category == "plugin" or tag == "server_log_plugin":
            return (
                "检查报错首条堆栈对应插件、插件版本、服务端核心版本、依赖插件是否缺失，"
                "以及最近是否更新过插件或配置。"
            )
        if category == "community" or tag == "server_log_community":
            return (
                "交由社区管理流程复核；确认处罚来源、玩家 UUID、触发规则、证据样本，"
                "避免误封。"
            )
        if category == "moderation" or tag == "server_log_auth":
            return (
                "检查权限组、白名单、登录插件、正版验证、UUID 模式，"
                "以及代理和后端的转发配置是否一致。"
            )
        if category == "cross_server" or tag == "server_log_cross_server":
            return (
                "检查 Velocity/Bungee 配置、player-info-forwarding-mode、forwarding secret、"
                "后端服务器地址、端口、转发协议和防火墙。"
            )
        if category == "economy" or tag == "server_log_economy":
            return (
                "检查 Vault、经济插件、商店插件、数据库连接、玩家余额数据和最近交易记录。"
            )
        if severity == "high":
            return "尽快查看 Minecraft latest.log 与压缩历史日志，确认根因后再处理。"
        if severity == "medium":
            return "继续观察同类 WARN/ERROR 是否扩大，必要时按日志文件和时间点人工复核。"
        return "持续观察运行日志即可。"

    # --- 运维备注（增强版）------------------------------------------------
    def _ops_notes(
        self,
        records: list[ObservationRecord],
        issues: list[dict[str, Any]],
        max_severity: str,
        any_alert: bool,
    ) -> tuple[list[str], dict[str, int]]:
        notes: list[str] = []
        counters: dict[str, int] = {
            "error": 0,
            "warn": 0,
            "performance": 0,
            "network": 0,
            "plugin": 0,
            "loop_suppressed": 0,
            "affected_servers": 0,
            "affected_backends": 0,
        }

        loop_summaries = [
            record for record in records if "loop_suppressed" in record.tags
        ]
        suppressed = sum(
            int((record.context or {}).get("loopSuppressed") or 0)
            for record in loop_summaries
        )
        counters["loop_suppressed"] = suppressed
        if suppressed:
            notes.append(
                f"已过滤 {suppressed} 条重复服务器报错循环日志，建议优先查看首条原始样本。"
            )

        for record in records:
            text = self._record_text(record)
            if any(marker in text for marker in ERROR_MARKERS):
                counters["error"] += 1
            if any(marker in text for marker in WARN_MARKERS):
                counters["warn"] += 1
            if any(marker in text for marker in PERFORMANCE_MARKERS):
                counters["performance"] += 1
            if any(marker in text for marker in NETWORK_MARKERS):
                counters["network"] += 1
            if any(marker in text for marker in PLUGIN_MARKERS):
                counters["plugin"] += 1

        affected_servers_set: set[str] = set()
        affected_backends_set: set[str] = set()
        for issue in issues:
            affected_servers_set.update(issue.get("affected_servers") or [])
            affected_backends_set.update(issue.get("affected_backends") or [])
        counters["affected_servers"] = len(affected_servers_set)
        counters["affected_backends"] = len(affected_backends_set)

        counter_parts = []
        if counters["error"]:
            counter_parts.append(f"ERROR {counters['error']} 条")
        if counters["warn"]:
            counter_parts.append(f"WARN {counters['warn']} 条")
        if counters["performance"]:
            counter_parts.append(f"PERFORMANCE {counters['performance']} 条")
        if counters["network"]:
            counter_parts.append(f"NETWORK {counters['network']} 条")
        if counters["plugin"]:
            counter_parts.append(f"PLUGIN {counters['plugin']} 条")
        if counter_parts:
            notes.append("窗口内 " + "，".join(counter_parts) + "。")

        scope_parts = []
        if counters["affected_servers"]:
            scope_parts.append(f"{counters['affected_servers']} 个服务器")
        if counters["affected_backends"]:
            scope_parts.append(f"{counters['affected_backends']} 个后端")
        if scope_parts:
            notes.append(
                f"影响 {'、'.join(scope_parts)}，最高严重级别 {max_severity}。"
            )

        if any_alert:
            triggered = next(
                (issue for issue in issues if issue.get("should_alert")),
                None,
            )
            if triggered:
                notes.append(
                    f"已达到告警条件：severity={triggered['severity']}，"
                    f"evidence_count={triggered['evidence_count']}。"
                )
            else:
                notes.append("已达到告警条件。")

        return notes, counters

    # --- 辅助 -------------------------------------------------------------
    def _categories_dict(self, categories: dict[str, list[str]]) -> dict[str, list[str]]:
        """按固定顺序输出 categories，包含新增的 network/plugin。"""
        return {
            "daily": categories.get("daily", []),
            "complaint": categories.get("complaint", []),
            "network": categories.get("network", []),
            "plugin": categories.get("plugin", []),
            "cross_server": categories.get("cross_server", []),
            "moderation": categories.get("moderation", []),
            "bug": categories.get("bug", []),
            "economy": categories.get("economy", []),
            "community": categories.get("community", []),
            "suggestion": categories.get("suggestion", []),
        }

    @staticmethod
    def _issue_terms(group: list[ObservationRecord]) -> list[str]:
        terms: list[str] = []
        all_markers = (
            CRITICAL_MARKERS
            + ERROR_MARKERS
            + WARN_MARKERS
            + PERFORMANCE_MARKERS
            + NETWORK_MARKERS
            + PLUGIN_MARKERS
            + COMMUNITY_MARKERS
        )
        for marker in all_markers:
            if any(marker in HeuristicReportBuilder._record_text(record) for record in group):
                terms.append(marker)
            if len(terms) >= 8:
                break
        return terms

    @staticmethod
    def _record_text(record: ObservationRecord) -> str:
        return f"{record.content} {' '.join(record.tags)}".lower()
