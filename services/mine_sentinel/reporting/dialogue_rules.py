"""Dialogue rule catalog for MineSentinel player-chat issue detection."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class DialogueRule:
    category: str
    tag: str
    title: str
    keywords: tuple[str, ...]
    urgent_terms: tuple[str, ...]
    suggested_action: str
    base_severity: str = "medium"


DIALOGUE_RULES: tuple[DialogueRule, ...] = (
    DialogueRule(
        category="complaint",
        tag="performance_lag",
        title="卡顿/延迟反馈",
        keywords=("卡", "延迟", "lag", "tps", "掉帧", "加载慢", "动不了", "服务器炸"),
        urgent_terms=("严重", "玩不了", "一直", "全服", "炸了", "崩了"),
        suggested_action="优先查看 TPS、内存、GC 和最近插件日志，确认是否为服务器侧性能问题。",
        base_severity="medium",
    ),
    DialogueRule(
        category="complaint",
        tag="disconnect_or_rollback",
        title="掉线/回档反馈",
        keywords=("掉线", "断开", "进不去", "回档", "rollback", "重连", "连接失败"),
        urgent_terms=("一直", "所有人", "全服", "又", "反复", "玩不了"),
        suggested_action="检查代理/后端连通性、最近重启记录和玩家所在后端日志。",
        base_severity="medium",
    ),
    DialogueRule(
        category="bug",
        tag="item_or_progress_loss",
        title="物品/进度丢失",
        keywords=("东西没了", "物品没了", "装备没了", "背包没了", "丢了", "进度没了", "数据没了"),
        urgent_terms=("全部", "辛苦", "很多", "重要", "赔", "恢复"),
        suggested_action="保留玩家名和时间点，人工核对存档、经济和背包相关插件数据。",
        base_severity="high",
    ),
    DialogueRule(
        category="bug",
        tag="feature_broken",
        title="玩法/功能异常",
        keywords=("不能用", "用不了", "打不开", "没反应", "报错", "bug", "异常", "坏了"),
        urgent_terms=("一直", "所有人", "全服", "关键", "主线", "任务"),
        suggested_action="按玩家、后端和时间点复现，优先查看对应功能插件日志。",
        base_severity="medium",
    ),
    DialogueRule(
        category="economy",
        tag="economy_or_shop_abuse",
        title="经济/商店异常",
        keywords=("钱", "金币", "余额", "商店", "价格", "刷物品", "复制", "dupe", "交易"),
        urgent_terms=("刷", "复制", "无限", "漏洞", "爆了", "清空"),
        suggested_action="核对经济流水、商店配置和相关玩家交易记录，确认范围后再人工处理。",
        base_severity="medium",
    ),
    DialogueRule(
        category="moderation",
        tag="cheat_or_grief_report",
        title="外挂/破坏举报",
        keywords=(
            "外挂",
            "作弊",
            "飞行",
            "透视",
            "矿透",
            "连点",
            "熊",
            "炸家",
            "偷东西",
            "举报",
            "利用 bug",
            "利用bug",
            "复制物品",
            "刷物品",
            "dupe",
        ),
        urgent_terms=("明显", "恶意", "一直", "大量", "封", "ban", "漏洞", "复制"),
        suggested_action="只做人工复核提醒，结合日志、回放或管理员现场观察后再处置。",
        base_severity="high",
    ),
    DialogueRule(
        category="moderation",
        tag="chat_conflict",
        title="聊天冲突/辱骂",
        keywords=("骂", "吵", "喷", "辱骂", "歧视", "骚扰", "威胁", "刷屏"),
        urgent_terms=("严重", "一直", "管理", "封", "退服"),
        suggested_action="人工复核上下文，必要时提醒冷静或按服务器规则处理。",
        base_severity="medium",
    ),
    DialogueRule(
        category="cross_server",
        tag="cross_server_transfer",
        title="跨服/传送异常",
        keywords=("跨服", "切服", "传送", "tp", "lobby", "大厅", "进服", "换服"),
        urgent_terms=("失败", "卡住", "回不去", "丢", "一直", "所有人"),
        suggested_action="检查代理转发、目标后端在线状态、传送插件和跨服权限配置。",
        base_severity="medium",
    ),
    DialogueRule(
        category="suggestion",
        tag="player_suggestion",
        title="玩家建议/体验请求",
        keywords=("建议", "希望", "能不能", "可不可以", "加个", "优化", "改一下"),
        urgent_terms=("很多人", "大家", "经常", "太麻烦"),
        suggested_action="记录为玩家体验反馈，管理员可集中评估优先级。",
        base_severity="low",
    ),
)


ALLOWED_CATEGORIES = {
    "complaint",
    "bug",
    "economy",
    "moderation",
    "suggestion",
    "cross_server",
}
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}
MAX_CUSTOM_RULES = 32
MAX_TERMS_PER_RULE = 32
MAX_TEXT_LENGTH = 160


def dialogue_rules_from_config(
    custom_rules: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> tuple[DialogueRule, ...]:
    """Return built-in rules plus sanitized server-specific custom rules."""

    return DIALOGUE_RULES + custom_dialogue_rules(custom_rules)


def custom_dialogue_rules(
    custom_rules: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> tuple[DialogueRule, ...]:
    rules: list[DialogueRule] = []
    used_tags = {rule.tag for rule in DIALOGUE_RULES}
    for index, item in enumerate((custom_rules or [])[:MAX_CUSTOM_RULES]):
        if not isinstance(item, dict):
            continue
        keywords = _terms(item.get("keywords"))
        if not keywords:
            continue
        category = _category(item.get("category"))
        tag = _unique_custom_tag(_tag(item.get("tag"), index), used_tags)
        used_tags.add(tag)
        title = _text(item.get("title"), tag)
        suggested_action = _text(
            item.get("suggested_action") or item.get("action"),
            "按服务器自定义规则人工复核相关玩家、时间和上下文。",
        )
        rules.append(
            DialogueRule(
                category=category,
                tag=tag,
                title=title,
                keywords=keywords,
                urgent_terms=_terms(item.get("urgent_terms")),
                suggested_action=suggested_action,
                base_severity=_severity(item.get("base_severity") or item.get("severity")),
            )
        )
    return tuple(rules)


def _terms(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_terms = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_terms = list(value)
    else:
        raw_terms = []

    terms: list[str] = []
    seen = set()
    for raw in raw_terms:
        term = _text(raw, "")
        normalized = term.lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
        if len(terms) >= MAX_TERMS_PER_RULE:
            break
    return tuple(terms)


def _category(value: Any) -> str:
    category = str(value or "complaint").strip()
    return category if category in ALLOWED_CATEGORIES else "complaint"


def _severity(value: Any) -> str:
    severity = str(value or "medium").strip().lower()
    return severity if severity in ALLOWED_SEVERITIES else "medium"


def _tag(value: Any, index: int) -> str:
    tag = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip().lower()).strip("_")
    return tag[:56] or f"rule_{index}"


def _unique_custom_tag(tag: str, used_tags: set[str]) -> str:
    base = f"custom_{tag}"
    if base not in used_tags:
        return base
    suffix = 2
    while True:
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 64 - len(suffix_text)]}{suffix_text}"
        if candidate not in used_tags:
            return candidate
        suffix += 1


def _text(value: Any, default: str) -> str:
    text = str(value or default).strip()
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 3] + "..."
    return text
