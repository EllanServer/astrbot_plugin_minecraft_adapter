"""Normalize AI report JSON back onto deterministic MineSentinel facts."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .common import format_locations, format_players


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
    """Preserves players, locations, metrics, and counts from fallback facts."""

    def normalize_report(
        self,
        data: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(fallback)
        result.update({key: value for key, value in data.items() if key in result})
        categories = result.get("categories")
        if not isinstance(categories, dict):
            categories = fallback["categories"]
        for key in (
            "daily",
            "complaint",
            "bug",
            "economy",
            "moderation",
            "suggestion",
            "cross_server",
        ):
            if not isinstance(categories.get(key), list):
                categories[key] = []
        result["categories"] = categories
        self.normalize_issues(result, fallback)
        if not isinstance(result.get("ops_notes"), list):
            result["ops_notes"] = fallback["ops_notes"]
        if not isinstance(result.get("chat_players"), list):
            result["chat_players"] = fallback.get("chat_players", [])
        if not result.get("chat_players_text"):
            result["chat_players_text"] = format_players(result["chat_players"])
        if not isinstance(result.get("dialogue_findings"), list):
            result["dialogue_findings"] = fallback.get("dialogue_findings", [])
        return result

    def normalize_issues(self, result: dict[str, Any], fallback: dict[str, Any]):
        if not isinstance(result.get("issues"), list):
            result["issues"] = fallback["issues"]
            return

        fallback_issues = [
            issue for issue in fallback.get("issues", []) if isinstance(issue, dict)
        ]
        fallback_lookup = _fallback_issue_index(fallback_issues)
        used_fallback_indexes: set[int] = set()
        normalized_issues = []
        for issue in result["issues"]:
            if not isinstance(issue, dict):
                continue
            matched_index, fallback_issue = self._match_fallback_issue(
                issue,
                fallback_issues,
                fallback_lookup,
                used_fallback_indexes,
            )
            if matched_index < 0:
                continue
            if matched_index >= 0:
                used_fallback_indexes.add(matched_index)
            self._normalize_structured_fields(issue, fallback_issue)
            self._normalize_players(issue, fallback_issue)
            self._normalize_counts(issue, fallback_issue)
            self._normalize_locations(issue, fallback_issue)
            self._normalize_metric_context(issue, fallback_issue)
            normalized_issues.append(issue)

        if normalized_issues and any(issue.get("players") for issue in normalized_issues):
            result["issues"] = normalized_issues
        else:
            result["issues"] = fallback["issues"]

    @staticmethod
    def _match_fallback_issue(
        issue: dict[str, Any],
        fallback_issues: list[dict[str, Any]],
        fallback_index: "_FallbackIssueIndex",
        used_indexes: set[int],
    ) -> tuple[int, dict[str, Any]]:
        key = (issue.get("category"), issue.get("tag"))
        incident_index = _as_int(issue.get("incident_index"))
        if incident_index is not None:
            for index in fallback_index["by_incident"].get((*key, incident_index), ()):
                if index in used_indexes:
                    continue
                return index, fallback_issues[index]

        for index in fallback_index["by_key"].get(key, ()):
            if index in used_indexes:
                continue
            return index, fallback_issues[index]
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
            "suggested_action",
            "should_alert",
        ):
            if issue.get(field) in (None, "", [], {}) and field in fallback_issue:
                issue[field] = fallback_issue[field]
        if issue.get("severity") not in {"low", "medium", "high", "critical"}:
            issue["severity"] = fallback_issue.get("severity", "medium")
        if not isinstance(issue.get("dialogue_terms"), list):
            terms = fallback_issue.get("dialogue_terms") or []
            issue["dialogue_terms"] = [str(term) for term in terms if term]
        samples = issue.get("evidence_samples")
        if not isinstance(samples, list) or not samples:
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
            issue["players_text"] = format_players(issue["players"])

        mentioned_players = issue.get("mentioned_players")
        if not isinstance(mentioned_players, list):
            mentioned_players = fallback_issue.get("mentioned_players") or []
        issue["mentioned_players"] = [
            str(player) for player in mentioned_players if player
        ]
        if not issue.get("mentioned_players_text"):
            issue["mentioned_players_text"] = format_players(
                issue["mentioned_players"]
            )

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

    @staticmethod
    def _normalize_metric_context(
        issue: dict[str, Any],
        fallback_issue: dict[str, Any],
    ):
        if not issue.get("metric_context_text"):
            issue["metric_context_text"] = fallback_issue.get(
                "metric_context_text",
                "",
            )
        if not isinstance(issue.get("metric_context"), dict):
            metric_context = fallback_issue.get("metric_context")
            if isinstance(metric_context, dict):
                issue["metric_context"] = metric_context


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_FallbackIssueIndex = dict[str, dict[tuple[Any, ...], list[int]]]


def _fallback_issue_index(
    fallback_issues: list[dict[str, Any]],
) -> _FallbackIssueIndex:
    by_key: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    by_incident: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, issue in enumerate(fallback_issues):
        key = (issue.get("category"), issue.get("tag"))
        by_key[key].append(index)
        incident_index = _as_int(issue.get("incident_index"))
        if incident_index is not None:
            by_incident[(*key, incident_index)].append(index)
    return {
        "by_key": dict(by_key),
        "by_incident": dict(by_incident),
    }
