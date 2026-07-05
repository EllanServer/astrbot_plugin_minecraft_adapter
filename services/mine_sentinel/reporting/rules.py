"""Rule-based analysis for Minecraft runtime logs.

分类优先级（先命中先返回）：
    community > chat_review > player_feedback > community_ops
    > complaint > network > plugin > cross_server > moderation > bug > economy > daily

严重级别：
    critical: 崩溃/OOM/watchdog/服务停止/代理大面积不可用
    high:     循环刷屏、多条 ERROR、插件加载失败、多服务器受影响、性能问题重复
              chat_review 出现威胁/隐私泄露/严重骚扰、community_ops 活动事故
    medium:   单条 ERROR、多条 WARN、单次性能警告、权限/登录/网络异常
              单次聊天违规/广告/可疑链接、活动奖励争议
    low:      单条 WARN、日常 join/quit/start/stop、普通玩家建议、普通活动公告

告警策略：
    critical 直告；high 默认 evidence_count >= min_evidence_count；
    medium 仅在多服务器/多后端、证据数较多或命中敏感词时告警；low 不告警。
    chat_review 默认不告警，除非 severity>=high / evidence_count>=5 / 命中威胁/开盒；
    player_feedback 通常不告警；community_ops 仅活动事故/奖励异常/大范围不满才告警。
"""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
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
        "grief",
        "cheat",
        "cheating",
        "anticheat",
        "anti-cheat",
        "xray",
        "fly",
        "speed",
        "reach",
        "kill aura",
        "killaura",
        "violation",
        "vl",
        # PR10: Vulcan 反作弊插件专用关键词，使 [Vulcan] 日志归入 community
        "vulcan",
        "封禁",
        "禁言",
        "踢出",
        "作弊",
        "外挂",
    ),
    "chat_review": (
        # 仅保留高置信度违规信号词；generic chat/message/said/tell/msg/whisper/pm
        # 会命中所有 [Async Chat Thread] 日志，导致 player_feedback 永远不可达，
        # 且与"辱骂/广告/骚扰/刷屏归 chat_review"的设计意图不符。
        # 真实日志验证：'ad' 子串会误判 dadada/already，'link'/'url' 误判正常技术讨论，
        # '私聊' 是正常常用词——均已移除。
        "swear",
        "profanity",
        "insult",
        "abuse",
        "harassment",
        "threat",
        "toxic",
        "advertising",
        # URL/外链信号（高置信度广告/引流指标）
        "discord.gg",
        "discord.com/invite",
        "http://",
        "https://",
        "www.",
        ".com/",
        ".cn/",
        # 中文辱骂/骚扰/广告信号
        "辱骂",
        "骂人",
        "脏话",
        "骚扰",
        "威胁",
        "开盒",
        "人肉",
        "刷屏",
        "代练",
        "代打",
        "出售账号",
        "卖号",
        "买号",
        "加群",
        "加微信",
        "加qq",
        "举报聊天",
    ),
    "player_feedback": (
        "suggest",
        "suggestion",
        "feedback",
        "idea",
        "request",
        "feature request",
        "proposal",
        "wish",
        "hope",
        "建议",
        "反馈",
        "想法",
        "希望",
        "能不能",
        "可不可以",
        "加个",
        "新增",
        "优化",
        "改进",
    ),
    "community_ops": (
        "event",
        "activity",
        "announcement",
        "notice",
        "reward",
        "vote",
        "poll",
        "rank",
        "season",
        "competition",
        "discord",
        "qq group",
        "community",
        "运营",
        "活动",
        "公告",
        "通知",
        "奖励",
        "投票",
        "赛季",
        "比赛",
        "招募",
        "群",
        "社区",
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
    "suggestion": (),
}

# 分类匹配优先级（先匹配先返回）。daily 永远兜底。
CLASSIFY_PRIORITY = (
    "community",
    "chat_review",
    "player_feedback",
    "community_ops",
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
COMMUNITY_MARKERS = CATEGORY_KEYS["community"]
CHAT_REVIEW_MARKERS = CATEGORY_KEYS["chat_review"]
PLAYER_FEEDBACK_MARKERS = CATEGORY_KEYS["player_feedback"]
COMMUNITY_OPS_MARKERS = CATEGORY_KEYS["community_ops"]
# URL/外链信号仅在 chat_message 标签的记录上触发 chat_review。
# 真实日志验证：QuickShop-Hikari 等插件的更新检查日志
# （[QuickShop-Hikari] Update here: https://modrinth.com/...）
# 会被 https:// 信号误判为 chat_review；插件更新日志不是聊天内容。
# 辱骂/代练/交易等中文信号对任何记录都适用（罕见误判）。
CHAT_REVIEW_URL_MARKERS = (
    "discord.gg",
    "discord.com/invite",
    "http://",
    "https://",
    "www.",
    ".com/",
    ".cn/",
)
CHAT_REVIEW_GENERAL_MARKERS = tuple(
    k for k in CHAT_REVIEW_MARKERS if k not in CHAT_REVIEW_URL_MARKERS
)
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
# chat_review 中的敏感词：命中即提级 high 并强制告警
CHAT_SENSITIVE_MARKERS = (
    "threat",
    "dox",
    "privacy",
    "威胁",
    "开盒",
    "人肉",
    "隐私",
)
# community_ops 中的事故关键词：命中即提级 high
COMMUNITY_OPS_SEVERE_MARKERS = (
    "奖励发放异常",
    "活动配置错误",
    "活动事故",
    "大范围玩家不满",
    "玩家不满",
    "事故",
)


# --- 关键词匹配辅助 -----------------------------------------------------
def _is_word_key(key: str) -> bool:
    """纯 ASCII 单字关键词（如 ad/pm/vl/fly）需要词边界匹配，避免误伤 load/road 等。"""
    return (
        bool(key)
        and key.isascii()
        and key.isalpha()
        and " " not in key
        and len(key) <= 6
    )


@lru_cache(maxsize=256)
def _word_boundary_regex(keys: tuple[str, ...]) -> "re.Pattern[str] | None":
    """把短英文词编译成单个词边界正则。"""
    word_keys = [k for k in keys if _is_word_key(k)]
    if not word_keys:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in word_keys) + r")\b")


def _keys_match(text: str, keys: tuple[str, ...]) -> bool:
    """关键词匹配：短 ASCII 单词用词边界，其余（短语/中文）用子串。"""
    word_re = _word_boundary_regex(keys)
    if word_re is not None and word_re.search(text) is not None:
        return True
    return any(key in text for key in keys if not _is_word_key(key))


def _format_timestamp(ts_ms: int) -> str:
    """把毫秒时间戳格式化为 HH:MM:SS（本地时间），用于 Vulcan 告警呈现。"""
    if not ts_ms:
        return ""
    import time as _time

    return _time.strftime("%H:%M:%S", _time.localtime(ts_ms / 1000))


class HeuristicReportBuilder:
    """Build deterministic fallback facts from SERVER_LOG records."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config
        # 预计算当前生效的分类优先级列表（应用 category_enabled / category_whitelist）。
        # daily 始终兜底，永远保留在末尾。
        self._active_priority: tuple[str, ...] = self._compute_active_priority()

    def _compute_active_priority(self) -> tuple[str, ...]:
        """根据 runtime_log.category_enabled / category_whitelist 计算生效分类。"""
        runtime = self.config.runtime_log
        whitelist = set(runtime.category_whitelist or ())
        disabled = set(
            cat for cat, enabled in (runtime.category_enabled or {}).items()
            if enabled is False
        )
        active = [
            cat
            for cat in CLASSIFY_PRIORITY
            if cat != "daily"
            and cat not in disabled
            and (not whitelist or cat in whitelist)
        ]
        # daily 永远兜底
        active.append("daily")
        return tuple(active)

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

        # PR10 v2: 玩家级刷屏检测——在分类前跑，给参与刷屏的记录打 chat_flood 标签，
        # 这样 classify() 能把它们强制归入 chat_review。
        # 刷屏是聚合行为（同一ID短时间大量重复/相似消息），不能靠单条形态识别。
        flood_events = self._detect_and_tag_floods(log_records)
        # PR10 v3: 玩家级重复违规检测——同一玩家在窗口内多次命中同类 chat_review
        # 关键词（如反复发链接、反复发代练广告）视为"行为"，打 chat_abuse 标签。
        # 单次命中只是"线索"，不强制 chat_review；重复命中才是"行为"。
        abuse_events = self._detect_and_tag_abuse(log_records)

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

        # PR10: 聊天热点总结 + Vulcan 反作弊结构化告警
        chat_topics = self._build_chat_topics(log_records, flood_events, abuse_events)
        vulcan_alerts = self._build_vulcan_alerts(log_records)

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
            "chat_topics": chat_topics,
            "vulcan_alerts": vulcan_alerts,
        }

    # --- 分类 -------------------------------------------------------------
    def classify(self, record: ObservationRecord) -> str:
        # PR10: daily_noise 标签优先级最高，强制归入 daily，避免正常 login/disconnect
        # 等被 moderation/network 关键词误判为异常事件。
        if "daily_noise" in record.tags:
            return "daily"
        # PR10 v3: 行为标签强制归入 chat_review（若该分类开启）。
        # 行为=刷屏(chat_flood) 或 重复违规(chat_abuse)，均由 build() 阶段
        # 基于玩家上下文检测后回填标签。单条关键词命中不强制归入 chat_review——
        # 单次命中只是"线索"，需要结合玩家上下文（同玩家是否重复发送同类内容）
        # 才能判定为"行为"。
        if "chat_review" in self._active_priority:
            if "chat_flood" in record.tags or "chat_abuse" in record.tags:
                return "chat_review"
        text = self._record_text(record)
        # 按当前生效的优先级列表匹配（已应用 category_enabled / category_whitelist），
        # daily 兜底。被关闭的分类直接跳过，记录会落到下一优先级或 daily。
        # 注意：chat_review 不再靠单条关键词命中触发——避免"玩家偶尔发一条链接"
        # 被误判为聊天审查违规。关键词命中只在 review_evidence 里作为"线索"呈现。
        for category in self._active_priority:
            if category == "daily":
                continue
            if category == "chat_review":
                continue  # 行为标签已在上面处理；单条关键词不再触发
            keys = CATEGORY_KEYS.get(category, ())
            if _keys_match(text, keys):
                return category
        return "daily"

    # --- Tag --------------------------------------------------------------
    def tag(self, record: ObservationRecord) -> str:
        text = self._record_text(record)
        level = str((record.context or {}).get("level") or "").lower()
        if "loop_suppressed" in record.tags:
            return f"server_log_loop_{level or 'warn'}"
        # PR10: Vulcan 反作弊告警单独打 tag，便于报告里按反作弊维度聚合呈现
        if "anticheat_vulcan" in record.tags:
            return "server_log_anticheat_vulcan"
        # 按分类优先级给 tag
        category = self.classify(record)
        tag_map = {
            "community": "server_log_community",
            "chat_review": "server_log_chat_review",
            "player_feedback": "server_log_player_feedback",
            "community_ops": "server_log_community_ops",
            "complaint": "server_log_performance",
            "network": "server_log_network",
            "plugin": "server_log_plugin",
            "cross_server": "server_log_cross_server",
            "moderation": "server_log_auth",
            "economy": "server_log_economy",
        }
        if category in tag_map:
            return tag_map[category]
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
        # PR10: 全员 daily_noise 的 group 强制 low，避免被 EWMA 突增/网络关键词
        # 提级，保证正常 login/disconnect/UUID 等绝不形成事件。
        if group and all("daily_noise" in record.tags for record in group):
            return "low"
        text = " ".join(self._record_text(record) for record in group)
        n = len(group)
        # 异常分数提级：模板计数突增（EWMA + 分位数）达到高分直接提级
        max_anomaly = 0.0
        for record in group:
            ctx = record.context or {}
            try:
                score = float(ctx.get("anomalyScore") or 0)
            except (TypeError, ValueError):
                score = 0.0
            if score > max_anomaly:
                max_anomaly = score
        if max_anomaly >= 0.8:
            return "critical"
        if max_anomaly >= 0.6:
            # 异常突增至少 high（除非其他规则已判 critical）
            base = self._severity_by_rules(text, n)
            return "critical" if base == "critical" else "high"
        return self._severity_by_rules(text, n)

    def _severity_by_rules(self, text: str, n: int) -> str:
        """关键词 + 计数驱动的 severity 判定（不含异常分数提级）。"""
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
        # high: chat_review 出现威胁/隐私泄露/严重骚扰
        if any(marker in text for marker in CHAT_SENSITIVE_MARKERS):
            return "high"
        # high: community_ops 活动事故/奖励异常/大范围玩家不满
        if any(marker in text for marker in COMMUNITY_OPS_SEVERE_MARKERS):
            return "high"
        # high: 多条 ERROR（substring，便于匹配 errors/failed 等）
        if any(marker in text for marker in ERROR_MARKERS):
            return "high" if n >= 2 else "medium"
        # high: 性能问题重复出现 >=3
        if any(marker in text for marker in PERFORMANCE_MARKERS):
            if n >= 3:
                return "high"
            return "medium" if n >= 1 else "low"
        # high: 网络错误 >=5
        if _keys_match(text, NETWORK_MARKERS):
            if n >= 5:
                return "high"
            return "medium" if n >= 2 else "low"
        # medium: chat_review 单次违规/广告/可疑链接/私聊举报
        if _keys_match(text, CHAT_REVIEW_MARKERS):
            return "medium" if n >= 1 else "low"
        # medium: community_ops 活动/奖励争议
        if _keys_match(text, COMMUNITY_OPS_MARKERS):
            return "medium" if n >= 1 else "low"
        # medium: player_feedback 多名玩家反复提出同类建议
        if _keys_match(text, PLAYER_FEEDBACK_MARKERS):
            return "medium" if n >= 3 else "low"
        # medium: 多条 WARN
        if any(marker in text for marker in WARN_MARKERS):
            return "medium" if n >= 2 else "low"
        # medium: 权限/登录/网络异常
        if _keys_match(text, CATEGORY_KEYS["moderation"]):
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
        text = " ".join(self._record_text(record) for record in group)
        # 循环刷屏 + high/critical 强制告警
        if "loop_suppressed" in text and severity in {"high", "critical"}:
            return True
        # 多服务器/多后端 + medium/high 强制告警
        multi_scope = len(affected_servers) >= 2 or len(affected_backends) >= 2
        if multi_scope and severity in {"medium", "high"}:
            return True
        # chat_review 特殊规则：默认不告警，除非 severity>=high / evidence_count>=5 / 命中敏感词
        # / 命中 chat_flood 标签（玩家级刷屏已强制 chat_review，需单独触发告警，否则审核漏报）
        if category == "chat_review":
            if severity in {"high", "critical"}:
                return True
            if any(marker in text for marker in CHAT_SENSITIVE_MARKERS):
                return True
            # 行为标签（刷屏/重复违规）强制告警——这些是基于玩家上下文判定的真行为
            if any("chat_flood" in record.tags or "chat_abuse" in record.tags for record in group):
                return True
            return evidence_count >= 5
        # player_feedback 通常不告警
        if category == "player_feedback":
            return False
        # community_ops 仅活动事故/奖励异常/大范围不满才告警（已由 severity=high 覆盖）
        if category == "community_ops":
            return severity in {"high", "critical"}
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
        if category == "community":
            return (
                "交由社区管理流程复核；确认处罚来源、玩家 UUID、触发规则、证据样本，"
                "避免误封。"
            )
        if category == "chat_review":
            return (
                "交由聊天审查流程复核；检查聊天原文、上下文、玩家 UUID、时间点、频道/私聊来源，"
                "并确认是否涉及辱骂、骚扰、广告、刷屏、威胁或隐私泄露。"
            )
        if category == "player_feedback":
            return (
                "整理为玩家反馈工单；记录玩家诉求、出现频率、影响范围和可执行性，"
                "交由社区运营或产品负责人评估。"
            )
        if category == "community_ops":
            return (
                "交由社区运营跟进；确认活动、公告、奖励、投票、赛季或玩家关系相关上下文，"
                "评估是否需要发布公告、回复玩家、调整活动规则或同步管理组。"
            )
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
        if category == "cross_server" or tag == "server_log_cross_server":
            return (
                "检查 Velocity/Bungee 配置、player-info-forwarding-mode、forwarding secret、"
                "后端服务器地址、端口、转发协议和防火墙。"
            )
        if category == "moderation" or tag == "server_log_auth":
            return (
                "检查权限组、白名单、登录插件、正版验证、UUID 模式，"
                "以及代理和后端的转发配置是否一致。"
            )
        if category == "economy" or tag == "server_log_economy":
            return (
                "检查 Vault、经济插件、商店插件、数据库连接、玩家余额数据和最近交易记录。"
            )
        if severity in {"high", "critical"}:
            return (
                "优先检查 latest.log、压缩历史日志、崩溃报告、最近部署/重启/插件更新记录，"
                "并评估是否需要回滚。"
            )
        if severity == "medium":
            return "继续观察同类 WARN/ERROR 是否扩大，并保留样本用于后续排查。"
        return "持续观察，无需立即处理。"

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
            "chat_review": 0,
            "player_feedback": 0,
            "community_ops": 0,
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
            if _keys_match(text, NETWORK_MARKERS):
                counters["network"] += 1
            if _keys_match(text, PLUGIN_MARKERS):
                counters["plugin"] += 1
            if _keys_match(text, CHAT_REVIEW_MARKERS):
                counters["chat_review"] += 1
            if _keys_match(text, PLAYER_FEEDBACK_MARKERS):
                counters["player_feedback"] += 1
            if _keys_match(text, COMMUNITY_OPS_MARKERS):
                counters["community_ops"] += 1

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

        ops_parts = []
        if counters["chat_review"]:
            ops_parts.append(f"聊天审查 {counters['chat_review']} 条")
        if counters["player_feedback"]:
            ops_parts.append(f"玩家建议 {counters['player_feedback']} 条")
        if counters["community_ops"]:
            ops_parts.append(f"社区运营 {counters['community_ops']} 条")
        if ops_parts:
            notes.append("窗口内 " + "，".join(ops_parts) + "。")

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
        """按固定顺序输出 categories，包含 network/plugin/chat_review/player_feedback/community_ops。"""
        return {
            "daily": categories.get("daily", []),
            "complaint": categories.get("complaint", []),
            "bug": categories.get("bug", []),
            "network": categories.get("network", []),
            "plugin": categories.get("plugin", []),
            "economy": categories.get("economy", []),
            "community": categories.get("community", []),
            "chat_review": categories.get("chat_review", []),
            "player_feedback": categories.get("player_feedback", []),
            "community_ops": categories.get("community_ops", []),
            "moderation": categories.get("moderation", []),
            "cross_server": categories.get("cross_server", []),
            "suggestion": categories.get("suggestion", []),
        }

    # --- 玩家级刷屏检测（PR10 v2）-----------------------------------------
    def _detect_and_tag_floods(
        self, records: list[ObservationRecord]
    ) -> list[dict[str, Any]]:
        """检测玩家级刷屏行为，给参与刷屏的记录打 chat_flood 标签。

        刷屏定义（百度百科+社区规则）：
        同一ID短时间集中发送大量重复或高度相似的信息。
        - 连续 5 条以上重复/相似 = 轻微刷屏
        - 连续 10 条以上 = 恶意刷屏

        三类刷屏：
        1. high_frequency: 60 秒内同一玩家发送 >=5 条消息
        2. repeat_content: 5 分钟内同一玩家发送 >=3 条相同/高度相似消息
        3. meaningless: 5 分钟内同一玩家发送 >=5 条无意义符号消息

        返回 flood_events 列表（用于 chat_topics.flood_players 呈现给 LLM）。
        """
        # 延迟导入避免循环依赖
        from ..runtime_log import _detect_chat_flood

        chat_records = [r for r in records if "chat_message" in r.tags]
        if not chat_records:
            return []
        floods_by_player = _detect_chat_flood(chat_records)
        if not floods_by_player:
            return []

        # 收集所有参与刷屏的记录 eventId，给它们打 chat_flood 标签
        flood_event_ids: set[str] = set()
        flood_events: list[dict[str, Any]] = []
        for player, events in floods_by_player.items():
            for event in events:
                flood_events.append(event)
        # 重新扫描记录，给参与刷屏窗口的记录打标签
        # 通过 player + 时间窗口匹配
        for record in chat_records:
            ctx = record.context or {}
            player = str(ctx.get("chatPlayer") or "").strip()
            if not player or player not in floods_by_player:
                continue
            ts = record.timestamp or 0
            for event in floods_by_player[player]:
                if (event["window_start_ms"] <= ts <= event["window_end_ms"]
                    and "chat_flood" not in record.tags):
                    record.tags.append("chat_flood")
                    # 记录刷屏类型到 context 供 LLM 呈现
                    ctx.setdefault("floodTypes", [])
                    if event["flood_type"] not in ctx["floodTypes"]:
                        ctx["floodTypes"].append(event["flood_type"])
                    break
        return flood_events

    # --- 玩家级重复违规检测（PR10 v3）-------------------------------------
    def _detect_and_tag_abuse(
        self, records: list[ObservationRecord]
    ) -> list[dict[str, Any]]:
        """检测玩家级重复违规行为，给重复命中的记录打 chat_abuse 标签。

        PR10 v3: 行为判断必须有上下文。单条关键词命中只是"线索"，
        同一玩家在窗口内多次命中"同类"关键词才构成"行为"：
        - 同玩家 >=2 条命中 URL 类（discord.gg/http/https） → 链接广告行为
        - 同玩家 >=2 条命中 交易广告类（代练/卖号/加群） → 交易广告行为
        - 同玩家 >=2 条命中 辱骂类 → 辱骂行为
        - 同玩家 >=1 条命中 敏感词（威胁/开盒/人肉） → 直接敏感行为

        返回 abuse_events 列表（用于 review_evidence 上下文呈现）。
        """
        chat_records = [r for r in records if "chat_message" in r.tags]
        if not chat_records:
            return []
        # 按玩家聚合
        player_records: dict[str, list[ObservationRecord]] = defaultdict(list)
        for record in chat_records:
            ctx = record.context or {}
            player = str(ctx.get("chatPlayer") or "").strip()
            if player:
                player_records[player].append(record)

        abuse_events: list[dict[str, Any]] = []
        abuse_record_ids: set[str] = set()
        for player, records_sorted in player_records.items():
            # 按类别统计命中
            hits_by_category: dict[str, list[tuple[ObservationRecord, list[str]]]] = defaultdict(list)
            for record in records_sorted:
                hit_keys = self._detect_chat_review_hits(record)
                if hit_keys:
                    category = self._classify_hit_keys(hit_keys)
                    hits_by_category[category].append((record, hit_keys))

            for category, hits in hits_by_category.items():
                # 敏感词：1 条即行为；其他类：>=2 条为行为
                is_behavior = (category == "sensitive") or (len(hits) >= 2)
                if not is_behavior:
                    continue
                # 给这些记录打 chat_abuse 标签
                for record, hit_keys in hits:
                    if "chat_abuse" not in (record.tags or []):
                        record.tags.append("chat_abuse")
                    ctx = record.context or {}
                    ctx.setdefault("abuseCategories", [])
                    if category not in ctx["abuseCategories"]:
                        ctx["abuseCategories"].append(category)
                    abuse_record_ids.add(record.event_id)
                abuse_events.append({
                    "player": player,
                    "category": category,
                    "hit_count": len(hits),
                    "samples": [
                        str((r.context or {}).get("chatMessage") or r.content).strip()[:150]
                        for r, _ in hits[:3]
                    ],
                })
        return abuse_events

    # --- 聊天热点总结（PR10）---------------------------------------------
    def _build_chat_topics(
        self,
        records: list[ObservationRecord],
        flood_events: list[dict[str, Any]] | None = None,
        abuse_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """从 chat_message 标签记录中聚合聊天热点。

        返回结构：
        - total_messages: 聊天消息总数
        - unique_players: 不同玩家数
        - top_players: 按消息数排序的活跃玩家，含样本消息
        - top_keywords: 高频关键词（去除停用词后）
        - sample_messages: 时间序样本消息
        - flood_players: 刷屏玩家列表（PR10 v2，玩家级时间窗口聚合检测）
        - abuse_players: 重复违规玩家列表（PR10 v3，基于玩家上下文的行为判断）
        - review_evidence: 需审查的聊天证据（行为 + 线索，含玩家上下文）

        chat_summary_enabled=false 时返回空字典。
        """
        if not self.config.runtime_log.chat_summary_enabled:
            return {}
        chat_records = [
            record for record in records if "chat_message" in record.tags
        ]
        if not chat_records:
            return {
                "total_messages": 0,
                "unique_players": 0,
                "top_players": [],
                "top_keywords": [],
                "sample_messages": [],
                "flood_players": [],
                "abuse_players": [],
                "review_evidence": [],
            }
        max_topics = max(1, self.config.runtime_log.chat_summary_max_topics)
        max_samples = max(1, self.config.runtime_log.chat_summary_max_samples)

        # 按玩家聚合
        player_messages: dict[str, list[ObservationRecord]] = defaultdict(list)
        all_messages: list[str] = []
        for record in chat_records:
            ctx = record.context or {}
            player = str(ctx.get("chatPlayer") or "").strip()
            message = str(ctx.get("chatMessage") or record.content).strip()
            player_messages[player].append(record)
            if message:
                all_messages.append(message)

        top_players_sorted = sorted(
            player_messages.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )[:max_topics]
        top_players = []
        for player, group in top_players_sorted:
            samples = [
                str((r.context or {}).get("chatMessage") or r.content).strip()
                for r in group[:max_samples]
            ]
            top_players.append(
                {
                    "player": player or "(unknown)",
                    "message_count": len(group),
                    "samples": [s for s in samples if s],
                }
            )

        # 高频关键词（简单分词 + 停用词过滤，不依赖外部 NLP 库）
        top_keywords = self._extract_top_keywords(all_messages, max_topics)

        # 时间序样本消息（覆盖整个窗口）
        sample_messages = []
        step = max(1, len(chat_records) // max_samples)
        for index in range(0, len(chat_records), step):
            record = chat_records[index]
            ctx = record.context or {}
            player = str(ctx.get("chatPlayer") or "").strip()
            message = str(ctx.get("chatMessage") or record.content).strip()
            if message:
                prefix = f"<{player}> " if player else ""
                sample_messages.append(prefix + message)
            if len(sample_messages) >= max_samples:
                break

        # PR10 v2: 刷屏玩家结构化呈现——把 flood_events 转成 LLM 友好格式
        flood_players = self._format_flood_players(flood_events or [])
        # PR10 v3: 重复违规玩家结构化呈现——同一玩家多次命中同类关键词
        abuse_players = self._format_abuse_players(abuse_events or [])

        # PR10 v3: 聊天审查证据——基于玩家上下文（行为 + 线索）
        review_evidence = self._build_chat_review_evidence(chat_records, max_samples=10)

        return {
            "total_messages": len(chat_records),
            "unique_players": len(player_messages),
            "top_players": top_players,
            "top_keywords": top_keywords,
            "sample_messages": sample_messages,
            "flood_players": flood_players,
            "abuse_players": abuse_players,
            "review_evidence": review_evidence,
        }

    def _format_flood_players(
        self, flood_events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """把 flood_events 转成 LLM 友好的 flood_players 列表。

        每个玩家一个条目，聚合该玩家的所有刷屏事件：
        - player: 玩家名
        - flood_types: 刷屏类型列表（high_frequency/repeat_content/meaningless）
        - total_messages: 窗口内消息总数
        - time_range: 时间范围 HH:MM:SS-HH:MM:SS
        - events: 各刷屏事件详情（type/window/message_count/samples）
        """
        if not flood_events:
            return []
        # 按玩家聚合
        by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in flood_events:
            by_player[event["player"]].append(event)
        result: list[dict[str, Any]] = []
        for player, events in by_player.items():
            all_ts = []
            for e in events:
                all_ts.append(e["window_start_ms"])
                all_ts.append(e["window_end_ms"])
            time_range = ""
            if all_ts:
                start = _format_timestamp(min(all_ts))
                end = _format_timestamp(max(all_ts))
                time_range = f"{start}-{end}"
            flood_types = sorted({e["flood_type"] for e in events})
            total_msgs = sum(e["message_count"] for e in events)
            # 收集样本（去重，最多 5 条）
            samples: list[str] = []
            seen: set[str] = set()
            for e in sorted(events, key=lambda x: x["window_start_ms"]):
                for s in e.get("samples", []):
                    if s not in seen:
                        samples.append(s)
                        seen.add(s)
                    if len(samples) >= 5:
                        break
                if len(samples) >= 5:
                    break
            result.append({
                "player": player,
                "flood_types": flood_types,
                "total_messages": total_msgs,
                "time_range": time_range,
                "events": [
                    {
                        "type": e["flood_type"],
                        "message_count": e["message_count"],
                        "time_range": (
                            f"{_format_timestamp(e['window_start_ms'])}-"
                            f"{_format_timestamp(e['window_end_ms'])}"
                        ),
                        "samples": e.get("samples", [])[:3],
                    }
                    for e in sorted(events, key=lambda x: x["window_start_ms"])
                ],
                "samples": samples,
            })
        # 按消息数降序
        result.sort(key=lambda x: x["total_messages"], reverse=True)
        return result

    def _format_abuse_players(
        self, abuse_events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """把 abuse_events 转成 LLM 友好的 abuse_players 列表。

        每个玩家一个条目，聚合该玩家的所有重复违规事件：
        - player: 玩家名
        - abuse_categories: 违规类别列表（url/abuse_language/trade_ad/sensitive）
        - total_hits: 命中总次数
        - events: 各违规事件详情（category/hit_count/samples）
        - samples: 样本原文（最多 5 条）
        """
        if not abuse_events:
            return []
        by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in abuse_events:
            by_player[event["player"]].append(event)
        result: list[dict[str, Any]] = []
        for player, events in by_player.items():
            categories = sorted({e["category"] for e in events})
            total_hits = sum(e["hit_count"] for e in events)
            samples: list[str] = []
            seen: set[str] = set()
            for e in events:
                for s in e.get("samples", []):
                    if s not in seen:
                        samples.append(s)
                        seen.add(s)
                    if len(samples) >= 5:
                        break
                if len(samples) >= 5:
                    break
            result.append({
                "player": player,
                "abuse_categories": categories,
                "total_hits": total_hits,
                "events": [
                    {
                        "category": e["category"],
                        "hit_count": e["hit_count"],
                        "samples": e.get("samples", [])[:3],
                    }
                    for e in events
                ],
                "samples": samples,
            })
        result.sort(key=lambda x: x["total_hits"], reverse=True)
        return result

    def _build_chat_review_evidence(
        self, chat_records: list[ObservationRecord], max_samples: int = 10
    ) -> list[dict[str, Any]]:
        """从聊天记录中提取需要审查的证据样本，基于玩家级行为上下文判断。

        PR10 v3: 行为判断必须有上下文。不再单条命中关键词就进证据，
        而是按玩家聚合，统计同一玩家在窗口内的：
        - 总消息数
        - 命中关键词的次数
        - 命中的关键词类型（URL/辱骂/代练/交易等）
        - 是否重复发送同类内容

        单次命中：作为"线索"呈现（reason=hint），不强制 chat_review
        重复命中：作为"行为"呈现（reason=abuse），强制 chat_review
        刷屏命中：作为"行为"呈现（reason=flood），强制 chat_review

        返回结构化列表，每条含：
        - player: 玩家名
        - player_total_messages: 该玩家窗口内总消息数（上下文）
        - player_hit_count: 该玩家命中关键词的消息数
        - message: 聊天原文（样本）
        - flood_types: 刷屏类型列表（参与刷屏时）
        - reason: 命中原因（flood/abuse/hint）
          flood=刷屏行为，abuse=重复关键词命中（行为），hint=单次命中（线索）
        - hit_keys: 命中的关键词
        - hit_category: 命中类别（url/abuse_language/trade_ad/sensitive）
        - time_text: HH:MM:SS 时间
        """
        # 第一步：按玩家聚合聊天记录，统计每个玩家的行为上下文
        player_records: dict[str, list[ObservationRecord]] = defaultdict(list)
        for record in chat_records:
            ctx = record.context or {}
            player = str(ctx.get("chatPlayer") or "").strip()
            if player:
                player_records[player].append(record)

        # 第二步：对每个玩家，找出命中关键词的记录并分类
        # 命中类别分组（用于判断是"单次线索"还是"重复行为"）
        evidence: list[dict[str, Any]] = []
        for player, records in player_records.items():
            records_sorted = sorted(records, key=lambda r: r.timestamp or 0)
            player_total = len(records_sorted)
            # 收集该玩家所有命中记录，按类别分组
            hit_records_by_category: dict[str, list[tuple[ObservationRecord, list[str]]]] = defaultdict(list)
            for record in records_sorted:
                ctx = record.context or {}
                tags = record.tags or []
                is_flood = "chat_flood" in tags
                if is_flood:
                    # 刷屏记录直接归入 flood 类别
                    hit_records_by_category["flood"].append((record, []))
                    continue
                # 检测关键词命中
                hit_keys = self._detect_chat_review_hits(record)
                if hit_keys:
                    category = self._classify_hit_keys(hit_keys)
                    hit_records_by_category[category].append((record, hit_keys))

            # 第三步：根据每个类别的命中次数判断是"行为"还是"线索"
            for category, hits in hit_records_by_category.items():
                hit_count = len(hits)
                # 行为判定阈值：同类命中 >=2 次视为"重复行为"（abuse）
                # 单次命中视为"线索"（hint），刷屏永远是"行为"（flood）
                if category == "flood":
                    reason = "flood"
                elif hit_count >= 2:
                    reason = "abuse"
                else:
                    reason = "hint"

                # 只把"行为"(flood/abuse) 进证据；"线索"(hint) 也进，但标记为 hint，
                # 让 LLM 能看到上下文区分严重性
                for record, hit_keys in hits[:3]:  # 每个玩家每类最多 3 条样本
                    ctx = record.context or {}
                    message = str(ctx.get("chatMessage") or record.content).strip()
                    flood_types = list(ctx.get("floodTypes") or [])
                    time_text = _format_timestamp(record.timestamp) if record.timestamp else ""
                    evidence.append({
                        "player": player,
                        "player_total_messages": player_total,
                        "player_hit_count": hit_count,
                        "message": message[:200],
                        "flood_types": flood_types,
                        "reason": reason,
                        "hit_keys": hit_keys[:5],
                        "hit_category": category if category != "flood" else "",
                        "time_text": time_text,
                    })
                    if len(evidence) >= max_samples:
                        return evidence
        return evidence

    @staticmethod
    def _detect_chat_review_hits(record: ObservationRecord) -> list[str]:
        """检测单条记录命中的 chat_review 关键词，返回命中的关键词列表。"""
        if "chat_message" not in (record.tags or []):
            return []
        content_lower = (record.content or "").lower()
        hits: list[str] = []
        for key in CHAT_REVIEW_GENERAL_MARKERS:
            if _is_word_key(key):
                if _word_boundary_regex((key,)) and _word_boundary_regex((key,)).search(content_lower):
                    hits.append(key)
            elif key in content_lower:
                hits.append(key)
        for key in CHAT_REVIEW_URL_MARKERS:
            if key in content_lower:
                hits.append(key)
        return hits

    @staticmethod
    def _classify_hit_keys(hit_keys: list[str]) -> str:
        """把命中的关键词归类，用于判断是哪类违规行为。

        返回类别：
        - url: URL/外链信号（discord.gg/http/https/www 等）
        - abuse_language: 辱骂/骚扰/威胁语言
        - trade_ad: 交易/代练/加群广告
        - sensitive: 敏感词（威胁/开盒/人肉/隐私）
        - other: 其他
        """
        url_set = set(CHAT_REVIEW_URL_MARKERS)
        sensitive_set = set(CHAT_SENSITIVE_MARKERS)
        trade_ad_keys = {"代练", "代打", "出售账号", "卖号", "买号", "加群", "加微信", "加qq", "举报聊天"}
        abuse_keys = {"swear", "profanity", "insult", "abuse", "harassment", "threat", "toxic",
                      "advertising", "辱骂", "骂人", "脏话", "骚扰", "威胁", "刷屏"}
        for key in hit_keys:
            if key in sensitive_set:
                return "sensitive"
        for key in hit_keys:
            if key in url_set:
                return "url"
        for key in hit_keys:
            if key in trade_ad_keys:
                return "trade_ad"
        for key in hit_keys:
            if key in abuse_keys:
                return "abuse_language"
        return "other"

    @staticmethod
    def _extract_top_keywords(
        messages: list[str], limit: int
    ) -> list[dict[str, Any]]:
        """从聊天消息中提取高频关键词。

        简单实现：英文按空格分词，过滤短词/停用词；中文按 2-3 字滑窗。
        不依赖 jieba 等分词库，结果用于 AI 进一步归纳的线索。
        """
        if not messages:
            return []
        stop_words = {
            "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
            "to", "of", "in", "on", "at", "for", "with", "by", "this", "that",
            "it", "as", "be", "have", "has", "do", "does", "i", "you", "he",
            "she", "we", "they", "me", "him", "her", "us", "them",
            "yes", "no", "ok", "okay", "lol", "haha", "ha", "le", "la", "de",
            "的", "了", "是", "在", "我", "你", "他", "她", "们", "个", "这",
            "那", "啊", "吧", "吗", "呢", "哦", "嗯", "呀",
        }
        counter: dict[str, int] = defaultdict(int)
        for message in messages:
            lowered = message.lower()
            # 英文词
            for word in re.findall(r"[a-z]{3,}", lowered):
                if word in stop_words:
                    continue
                counter[word] += 1
            # 中文 2-3 字滑窗
            for match in re.finditer(r"[\u4e00-\u9fa5]{2,}", message):
                segment = match.group(0)
                # 2-gram
                for i in range(len(segment) - 1):
                    gram = segment[i : i + 2]
                    if gram[0] in stop_words or gram[1] in stop_words:
                        continue
                    counter[gram] += 1
        # 至少出现 2 次才算热点
        hot = [(kw, count) for kw, count in counter.items() if count >= 2]
        hot.sort(key=lambda item: item[1], reverse=True)
        return [
            {"keyword": kw, "count": count} for kw, count in hot[:limit] if count > 0
        ]

    # --- Vulcan 反作弊告警结构化（PR10）-----------------------------------
    def _build_vulcan_alerts(
        self, records: list[ObservationRecord]
    ) -> dict[str, Any]:
        """从 anticheat_vulcan 标签记录中提取结构化告警。

        返回结构（应对海量告警，如真实日志 4202 条/2 玩家的场景）：
        - total: 告警总数
        - unique_players: 涉及不同玩家数
        - unique_checks: 涉及不同检查类型数
        - by_player: [{player, count, top_checks: [(check, count)]}] 按告警数降序
        - by_check: [{check, count, players: [player]}] 按告警数降序
        - time_range: {start, end} 最早/最晚告警时间文本
        - samples: 最多 20 条原始告警（time_text + player + check），按时间序

        Vulcan 检测关闭时返回空字典。
        """
        if not self.config.runtime_log.vulcan_detect_enabled:
            return {}
        vulcan_records = [
            record for record in records if "anticheat_vulcan" in record.tags
        ]
        if not vulcan_records:
            return {}
        vulcan_records.sort(key=lambda r: r.timestamp)

        # 按玩家聚合
        player_alerts: dict[str, list[tuple[str, ObservationRecord]]] = defaultdict(list)
        check_alerts: dict[str, list[tuple[str, ObservationRecord]]] = defaultdict(list)
        for record in vulcan_records:
            ctx = record.context or {}
            player = str(ctx.get("vulcanPlayer") or "").strip() or "(unknown)"
            check = str(ctx.get("vulcanCheck") or "").strip() or "(unknown)"
            ts_text = _format_timestamp(int(record.timestamp or 0))
            player_alerts[player].append((check, record))
            check_alerts[check].append((player, record))

        # by_player 排序
        by_player = []
        for player, items in sorted(
            player_alerts.items(), key=lambda kv: len(kv[1]), reverse=True
        ):
            check_counter: dict[str, int] = defaultdict(int)
            for check, _ in items:
                check_counter[check] += 1
            top_checks = sorted(
                check_counter.items(), key=lambda kv: kv[1], reverse=True
            )[:3]
            by_player.append(
                {
                    "player": player,
                    "count": len(items),
                    "top_checks": [
                        {"check": c, "count": n} for c, n in top_checks
                    ],
                }
            )

        # by_check 排序
        by_check = []
        for check, items in sorted(
            check_alerts.items(), key=lambda kv: len(kv[1]), reverse=True
        ):
            players = sorted({p for p, _ in items})
            by_check.append(
                {
                    "check": check,
                    "count": len(items),
                    "players": players,
                }
            )

        # 时间范围
        first_ts = int(vulcan_records[0].timestamp or 0)
        last_ts = int(vulcan_records[-1].timestamp or 0)
        time_range = {
            "start": _format_timestamp(first_ts),
            "end": _format_timestamp(last_ts),
        }

        # 样本（最多 20 条，覆盖整个时间范围）
        sample_records = vulcan_records
        max_samples = 20
        if len(sample_records) > max_samples:
            step = max(1, len(sample_records) // max_samples)
            sample_records = [sample_records[i] for i in range(0, len(sample_records), step)][:max_samples]
        samples = []
        for record in sample_records:
            ctx = record.context or {}
            samples.append(
                {
                    "time_text": _format_timestamp(int(record.timestamp or 0)),
                    "server_id": record.server_id or "",
                    "player": str(ctx.get("vulcanPlayer") or "").strip(),
                    "check": str(ctx.get("vulcanCheck") or "").strip(),
                }
            )

        return {
            "total": len(vulcan_records),
            "unique_players": len(player_alerts),
            "unique_checks": len(check_alerts),
            "by_player": by_player,
            "by_check": by_check,
            "time_range": time_range,
            "samples": samples,
        }

    @staticmethod
    def _issue_terms(group: list[ObservationRecord]) -> list[str]:
        terms: list[str] = []
        # severity markers 用 substring（便于匹配 errors/failed 等）
        substring_markers = CRITICAL_MARKERS + ERROR_MARKERS + WARN_MARKERS + PERFORMANCE_MARKERS
        for marker in substring_markers:
            if any(marker in HeuristicReportBuilder._record_text(record) for record in group):
                terms.append(marker)
            if len(terms) >= 8:
                return terms
        # 分类 markers 用 _keys_match（与 classify 一致）
        category_marker_groups = (
            NETWORK_MARKERS,
            PLUGIN_MARKERS,
            COMMUNITY_MARKERS,
            CHAT_REVIEW_MARKERS,
            PLAYER_FEEDBACK_MARKERS,
            COMMUNITY_OPS_MARKERS,
        )
        combined_text = " ".join(
            HeuristicReportBuilder._record_text(record) for record in group
        )
        for markers in category_marker_groups:
            for marker in markers:
                if _is_word_key(marker):
                    # 词边界匹配的交给 _keys_match 整体判断，单独词不重复输出
                    continue
                if marker in combined_text and marker not in terms:
                    terms.append(marker)
                    if len(terms) >= 8:
                        return terms
        # 补齐词边界命中的短词
        for markers in category_marker_groups:
            word_re = _word_boundary_regex(markers)
            if word_re is None:
                continue
            for match in word_re.finditer(combined_text):
                word = match.group(0)
                if word not in terms:
                    terms.append(word)
                    if len(terms) >= 8:
                        return terms
        return terms

    @staticmethod
    def _record_text(record: ObservationRecord) -> str:
        return f"{record.content} {' '.join(record.tags)}".lower()
