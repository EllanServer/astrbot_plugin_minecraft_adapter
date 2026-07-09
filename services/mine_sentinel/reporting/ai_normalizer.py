"""Normalize AI report JSON back onto deterministic runtime-log facts."""

from __future__ import annotations

import json
import re
from typing import Any

from .common import format_locations
from .sections import normalize_report_sections

# LLM 产出的自由文本字段最大长度。超过截断，防止 LLM 滥用输出导致
# 报告膨胀或借机注入大段文本。
MAX_FREE_TEXT_CHARS = 500
# 不可信内容中常见的指令注入短语，从 LLM 输出文本中剥离，避免注入
# 文本经报告渲染进入管理员群。这并非完备防御（提示注入根本性缓解在
# prompt 隔离层），但能挡掉最直接的"请执行 X"类输出。
_INJECTION_PATTERNS = [
    re.compile(r"忽略(?:以上|前面|先前).{0,20}指令", re.IGNORECASE),
    re.compile(r"ignore (?:the |all |previous )?(?:above |prior )?instructions?", re.IGNORECASE),
    re.compile(r"系统(?:提示|指令|消息)", re.IGNORECASE),
    re.compile(r"system (?:prompt|instruction|message)", re.IGNORECASE),
    re.compile(r"请(?:立即|马上|现在)?(?:执行|运行|调用).{0,30}", re.IGNORECASE),
    re.compile(r"<evidence>|</evidence>", re.IGNORECASE),
]
# 控制字符（保留换行 \n 与制表符 \t）
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_free_text(value: Any, max_chars: int = MAX_FREE_TEXT_CHARS) -> str:
    """对 LLM 产出的自由文本字段做最小清洗：

    - 非 str 强制 str 化；
    - 剥离控制字符；
    - 移除常见指令注入短语；
    - 截断到 max_chars。

    这一层是纵深防御，不能替代 prompt 侧的 <evidence> 隔离。
    """
    text = str(value or "")
    text = _CONTROL_RE.sub("", text)
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub("", text)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text.strip()


def _sanitize_free_text_list(values: Any, max_chars: int = MAX_FREE_TEXT_CHARS) -> list[str]:
    if not isinstance(values, list):
        return []
    return [sanitize_free_text(v, max_chars) for v in values if v]


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def repair_json_object_text(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    return match.group(0) if match else ""


class AIReportNormalizer:
    """Preserves locations, evidence, and counts from fallback facts."""

    def normalize_report(
        self,
        data: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(fallback)
        result.update({key: value for key, value in data.items() if key in result})
        ai_categories = data.get("categories")
        if not isinstance(ai_categories, dict):
            ai_categories = {}
        fallback_categories = fallback.get("categories") or {}
        categories = {}
        for key in (
            "daily",
            "complaint",
            "bug",
            "network",
            "plugin",
            "economy",
            "community",
            "chat_review",
            "player_feedback",
            "community_ops",
            "moderation",
            "cross_server",
            "suggestion",
        ):
            fallback_items = fallback_categories.get(key) or []
            ai_items = ai_categories.get(key)
            if fallback_items and isinstance(ai_items, list):
                categories[key] = ai_items
            elif isinstance(fallback_items, list):
                categories[key] = fallback_items
            else:
                categories[key] = []
        result["categories"] = categories
        if not fallback.get("vulcan_alerts"):
            result["vulcan_alerts"] = fallback.get("vulcan_alerts", {})
        if not fallback.get("chat_topics"):
            result["chat_topics"] = fallback.get("chat_topics", {})
        self.normalize_issues(result, fallback)
        if not isinstance(result.get("ops_notes"), list):
            result["ops_notes"] = fallback.get("ops_notes", [])
        if not isinstance(result.get("incident_findings"), list):
            result["incident_findings"] = fallback.get("incident_findings", [])
        result["report_sections"] = normalize_report_sections(
            data.get("report_sections"),
            result,
        )
        return result

    def normalize_issues(self, result: dict[str, Any], fallback: dict[str, Any]):
        fallback_issues_raw = fallback.get("issues", [])
        if not isinstance(fallback_issues_raw, list):
            fallback_issues_raw = []
        if not isinstance(result.get("issues"), list):
            result["issues"] = fallback_issues_raw
            return

        fallback_issues = [
            issue for issue in fallback_issues_raw if isinstance(issue, dict)
        ]
        used_fallback_indexes: set[int] = set()
        normalized_issues = []
        for issue in result["issues"]:
            if not isinstance(issue, dict):
                continue
            fallback_index, fallback_issue = self._match_fallback_issue(
                issue,
                fallback_issues,
                used_fallback_indexes,
            )
            if fallback_index < 0:
                continue
            if fallback_index >= 0:
                used_fallback_indexes.add(fallback_index)
            self._normalize_structured_fields(issue, fallback_issue)
            self._normalize_players(issue, fallback_issue)
            self._normalize_counts(issue, fallback_issue)
            self._normalize_locations(issue, fallback_issue)
            normalized_issues.append(issue)

        if normalized_issues:
            result["issues"] = normalized_issues
        else:
            result["issues"] = fallback_issues_raw

    @staticmethod
    def _match_fallback_issue(
        issue: dict[str, Any],
        fallback_issues: list[dict[str, Any]],
        used_indexes: set[int],
    ) -> tuple[int, dict[str, Any]]:
        key = (issue.get("category"), issue.get("tag"))
        incident_index = _as_int(issue.get("incident_index"))
        if incident_index is not None:
            for index, fallback_issue in enumerate(fallback_issues):
                if index in used_indexes:
                    continue
                fallback_key = (fallback_issue.get("category"), fallback_issue.get("tag"))
                if (
                    fallback_key == key
                    and _as_int(fallback_issue.get("incident_index")) == incident_index
                ):
                    return index, fallback_issue

        for index, fallback_issue in enumerate(fallback_issues):
            if index in used_indexes:
                continue
            fallback_key = (fallback_issue.get("category"), fallback_issue.get("tag"))
            if fallback_key == key:
                return index, fallback_issue
        return -1, {}

    @staticmethod
    def _normalize_structured_fields(
        issue: dict[str, Any],
        fallback_issue: dict[str, Any],
    ):
        fallback_incident = _as_int(fallback_issue.get("incident_index"))
        issue_incident = _as_int(issue.get("incident_index"))
        if fallback_incident is not None:
            issue["incident_index"] = fallback_incident
        elif issue_incident is not None:
            issue["incident_index"] = issue_incident
        for field in (
            "source_tag",
            "first_seen_ts",
            "last_seen_ts",
            "urgent_signal_count",
        ):
            if field not in issue and field in fallback_issue:
                issue[field] = fallback_issue[field]
        # 对 LLM 产出的自由文本/列表字段做清洗，防止提示注入文本经报告
        # 渲染进入管理员群。
        issue["suggested_action"] = sanitize_free_text(issue.get("suggested_action"))
        issue["incident_title"] = sanitize_free_text(
            issue.get("incident_title") or issue.get("title")
        )
        issue["ops_categories"] = _sanitize_free_text_list(issue.get("ops_categories"))
        issue["ops_subtypes"] = _sanitize_free_text_list(issue.get("ops_subtypes"))
        issue["ops_impacts"] = _sanitize_free_text_list(issue.get("ops_impacts"))
        if not isinstance(issue.get("issue_terms"), list):
            terms = fallback_issue.get("issue_terms") or []
            issue["issue_terms"] = [str(term) for term in terms if term]
        else:
            issue["issue_terms"] = _sanitize_free_text_list(issue.get("issue_terms"))
        if not isinstance(issue.get("evidence_samples"), list):
            samples = fallback_issue.get("evidence_samples") or []
            issue["evidence_samples"] = [str(sample) for sample in samples if sample]

    @staticmethod
    def _normalize_players(
        issue: dict[str, Any],
        fallback_issue: dict[str, Any],
    ):
        players = issue.get("players") or issue.get("player_names")
        if not isinstance(players, list):
            players = fallback_issue.get("players") or []
        issue["players"] = [str(player) for player in players if player]
        if not issue.get("players_text"):
            issue["players_text"] = "无"

        mentioned_players = issue.get("mentioned_players")
        if not isinstance(mentioned_players, list):
            mentioned_players = fallback_issue.get("mentioned_players") or []
        issue["mentioned_players"] = [
            str(player) for player in mentioned_players if player
        ]
        if not issue.get("mentioned_players_text"):
            issue["mentioned_players_text"] = "无"

    @staticmethod
    def _normalize_counts(
        issue: dict[str, Any],
        fallback_issue: dict[str, Any],
    ):
        for count_field in (
            "evidence_count",
            "signal_count",
            "distinct_message_count",
            "unique_players",
        ):
            if count_field not in issue and count_field in fallback_issue:
                issue[count_field] = fallback_issue[count_field]

    @staticmethod
    def _normalize_locations(
        issue: dict[str, Any],
        fallback_issue: dict[str, Any],
    ):
        for list_field in (
            "affected_servers",
            "affected_backends",
            "affected_locations",
        ):
            values = issue.get(list_field)
            if not isinstance(values, list):
                values = fallback_issue.get(list_field) or []
            issue[list_field] = [str(value) for value in values if value]
        if not issue.get("affected_locations_text"):
            issue["affected_locations_text"] = format_locations(
                issue.get("affected_locations") or []
            )

def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
