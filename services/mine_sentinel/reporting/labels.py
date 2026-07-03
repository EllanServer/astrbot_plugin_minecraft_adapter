"""Chinese label catalog for MineSentinel report presentation."""

from __future__ import annotations

import re
from typing import Any

from .dialogue_rules import DIALOGUE_RULES


CATEGORY_TITLES = {
    "daily": "日常观察",
    "complaint": "玩家投诉/性能反馈",
    "bug": "玩法/功能异常",
    "economy": "经济/商店异常",
    "moderation": "管理/违规反馈",
    "suggestion": "玩家建议/体验请求",
    "cross_server": "跨服/传送异常",
}

GENERIC_TAG_TITLES = {
    "server_metrics": "服务器指标",
    "player_join": "玩家上线",
    "player_quit": "玩家离线",
    "chat": "聊天记录",
    "plugin_error": "插件错误",
    "server_switch": "跨服切换",
}


class LabelCatalog:
    """Translate deterministic report categories and tags into reader-facing text."""

    def __init__(
        self,
        tag_titles: dict[str, str] | None = None,
        category_titles: dict[str, str] | None = None,
        generic_tag_titles: dict[str, str] | None = None,
    ):
        self.tag_titles = tag_titles or {rule.tag: rule.title for rule in DIALOGUE_RULES}
        self.category_titles = category_titles or CATEGORY_TITLES
        self.generic_tag_titles = generic_tag_titles or GENERIC_TAG_TITLES

    def issue_title(self, issue: dict[str, Any]) -> str:
        title = str(issue.get("title") or "").strip()
        if title and not self.looks_like_raw_tag(title):
            return title
        tag_title = self.tag_title(issue.get("tag"))
        if tag_title:
            return tag_title
        category = str(issue.get("category") or "").lower()
        return self.category_titles.get(category) or "玩家反馈"

    def tag_title(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""

        parts = [part.strip() for part in re.split(r"[,，;；]+", raw) if part.strip()]
        if len(parts) > 1:
            return "、".join(
                unique_text(
                    [label for label in (self.single_tag_title(part) for part in parts) if label]
                )
            )
        return self.single_tag_title(raw)

    def single_tag_title(self, value: str) -> str:
        tag = value.strip().lower()
        if not tag:
            return ""
        if tag.startswith("dialogue:"):
            tag = tag.split(":", 1)[1]
        return self.tag_titles.get(tag) or self.generic_tag_titles.get(tag) or ""

    def looks_like_raw_tag(self, value: str) -> bool:
        text = value.strip()
        if not text:
            return False
        if "," in text or "，" in text or ";" in text or "；" in text:
            return any(self.single_tag_title(part) for part in re.split(r"[,，;；]+", text))
        return bool(re.fullmatch(r"[a-z0-9_:-]+", text) and self.single_tag_title(text))


def unique_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = re.sub(r"\s+", "", value).lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


DEFAULT_LABELS = LabelCatalog()
