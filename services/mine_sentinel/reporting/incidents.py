"""Incident-level aggregation for MineSentinel report issues."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


INCIDENT_MERGE_WINDOW_MS = 5 * 60 * 1000
ACTIONABLE_SEVERITIES = {"medium", "high", "critical"}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
MODERATION_TAGS = {"cheat_or_grief_report", "chat_conflict"}
SUGGESTION_TAGS = {"player_suggestion"}
ABUSE_HINTS = (
    "复制",
    "dupe",
    "刷物品",
    "外挂",
    "作弊",
    "飞行",
    "透视",
    "举报",
    "炸家",
    "偷东西",
)


@dataclass
class IncidentGroup:
    """A reader-facing incident assembled from one or more deterministic issues."""

    family: str
    scopes: set[str] = field(default_factory=set)
    issues: list[dict[str, Any]] = field(default_factory=list)
    start_ts: int = 0
    end_ts: int = 0
    max_severity: str = "low"

    @classmethod
    def from_issue(cls, issue: dict[str, Any]) -> "IncidentGroup":
        start, end = issue_time_bounds(issue)
        return cls(
            family=issue_family(issue),
            scopes=set(issue_scopes(issue)),
            issues=[issue],
            start_ts=start,
            end_ts=end,
            max_severity=str(issue.get("severity") or "low").lower(),
        )

    @classmethod
    def from_precomputed(
        cls,
        issue: dict[str, Any],
        family: str,
        scopes: list[str],
        start: int,
        end: int,
    ) -> "IncidentGroup":
        return cls(
            family=family,
            scopes=set(scopes),
            issues=[issue],
            start_ts=start,
            end_ts=end,
            max_severity=str(issue.get("severity") or "low").lower(),
        )

    def add(self, issue: dict[str, Any]) -> set[str]:
        return self.add_precomputed(
            issue,
            issue_scopes(issue),
            *issue_time_bounds(issue),
        )

    def add_precomputed(
        self,
        issue: dict[str, Any],
        scopes: list[str],
        start: int,
        end: int,
    ) -> set[str]:
        self.issues.append(issue)
        added_scopes = set(scopes) - self.scopes
        self.scopes.update(scopes)
        if start:
            self.start_ts = min(self.start_ts or start, start)
        if end:
            self.end_ts = max(self.end_ts, end)
        if severity_rank(issue) > SEVERITY_RANK.get(self.max_severity, 0):
            self.max_severity = str(issue.get("severity") or "low").lower()
        return added_scopes


class IssuePolicy:
    """Classify issues for presentation and report dispatch decisions."""

    @staticmethod
    def actionable_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            issue
            for issue in issues
            if not is_passive_issue(issue)
            and (
                issue.get("should_alert")
                or str(issue.get("severity") or "").lower() in ACTIONABLE_SEVERITIES
            )
        ]

    @staticmethod
    def is_metric_issue(issue: dict[str, Any]) -> bool:
        return is_metric_issue(issue)

    @staticmethod
    def is_moderation_issue(issue: dict[str, Any]) -> bool:
        return (
            str(issue.get("category") or "").lower() == "moderation"
            or str(issue.get("tag") or "").lower() == "chat_conflict"
        )


class IncidentGrouper:
    """Group issue-level facts into incident-level summaries."""

    def __init__(self, merge_window_ms: int = INCIDENT_MERGE_WINDOW_MS):
        self.merge_window_ms = max(0, int(merge_window_ms))

    def group(self, issues: list[dict[str, Any]]) -> list[IncidentGroup]:
        groups: list[IncidentGroup] = []
        group_index = _IncidentGroupIndex()
        for issue in sorted(issues, key=issue_sort_key):
            if is_metric_issue(issue):
                continue
            family = issue_family(issue)
            scopes = issue_scopes(issue)
            scope_values = set(scopes)
            issue_start, issue_end = issue_time_bounds(issue)
            placed = False
            min_group_end = (
                issue_start - self.merge_window_ms if issue_start else None
            )
            for group in group_index.candidates(family, scopes, min_group_end):
                if self.can_merge_precomputed(
                    group,
                    family,
                    scope_values,
                    issue_start,
                    issue_end,
                ):
                    added_scopes = group.add_precomputed(
                        issue,
                        scopes,
                        issue_start,
                        issue_end,
                    )
                    group_index.update_group(group, added_scopes)
                    placed = True
                    break
            if not placed:
                group = IncidentGroup.from_precomputed(
                    issue,
                    family,
                    scopes,
                    issue_start,
                    issue_end,
                )
                groups.append(group)
                group_index.add_group(group)
        groups.sort(key=incident_sort_key)
        return groups

    def can_merge(self, group: IncidentGroup, issue: dict[str, Any]) -> bool:
        return self.can_merge_precomputed(
            group,
            issue_family(issue),
            set(issue_scopes(issue)),
            *issue_time_bounds(issue),
        )

    def can_merge_precomputed(
        self,
        group: IncidentGroup,
        family: str,
        issue_scope_values: set[str],
        issue_start: int,
        issue_end: int,
    ) -> bool:
        if group.family != family:
            return False
        if group.scopes and issue_scope_values and group.scopes.isdisjoint(issue_scope_values):
            return False
        return self.times_close_bounds(group, issue_start, issue_end)

    def times_close(self, group: IncidentGroup, issue: dict[str, Any]) -> bool:
        return self.times_close_bounds(group, *issue_time_bounds(issue))

    def times_close_bounds(
        self,
        group: IncidentGroup,
        issue_start: int,
        issue_end: int,
    ) -> bool:
        if not issue_start or not issue_end or not group.start_ts or not group.end_ts:
            return True
        return (
            issue_start <= group.end_ts + self.merge_window_ms
            and group.start_ts <= issue_end + self.merge_window_ms
        )


class _IncidentGroupIndex:
    """Preserve group order while narrowing merge candidates by family/scope."""

    def __init__(self):
        self._groups_by_key: dict[tuple[str, str], list[IncidentGroup]] = defaultdict(list)
        self._order: dict[int, int] = {}

    def add_group(self, group: IncidentGroup):
        group_id = id(group)
        if group_id not in self._order:
            self._order[group_id] = len(self._order)
        for scope in group.scopes:
            self._groups_by_key[(group.family, scope)].append(group)

    def update_group(self, group: IncidentGroup, added_scopes: set[str]):
        for scope in added_scopes:
            self._groups_by_key[(group.family, scope)].append(group)

    def candidates(
        self,
        family: str,
        scopes: list[str],
        min_end_ts: int | None = None,
    ) -> list[IncidentGroup]:
        candidates: list[IncidentGroup] = []
        seen: set[int] = set()
        for scope in scopes:
            for group in self._active_groups((family, scope), min_end_ts):
                group_id = id(group)
                if group_id in seen:
                    continue
                seen.add(group_id)
                candidates.append(group)
        candidates.sort(key=lambda group: self._order.get(id(group), 0))
        return candidates

    def _active_groups(
        self,
        key: tuple[str, str],
        min_end_ts: int | None,
    ) -> list[IncidentGroup] | tuple[IncidentGroup, ...]:
        groups = self._groups_by_key.get(key)
        if not groups:
            return ()
        if min_end_ts is None:
            return groups

        active = [
            group
            for group in groups
            if not group.end_ts or group.end_ts >= min_end_ts
        ]
        if len(active) != len(groups):
            self._groups_by_key[key] = active
        return active


def is_passive_issue(issue: dict[str, Any]) -> bool:
    return str(issue.get("category") or "").lower() == "daily" or is_metric_issue(issue)


def is_metric_issue(issue: dict[str, Any]) -> bool:
    tag = str(issue.get("tag") or "").lower()
    source_tag = str(issue.get("source_tag") or "").lower()
    category = str(issue.get("category") or "").lower()
    return tag == "server_metrics" or (
        category == "daily" and ("metric" in tag or "metric" in source_tag)
    )


def issue_sort_key(issue: dict[str, Any]) -> tuple[int, int, str]:
    start, end = issue_time_bounds(issue)
    ts = start or end or 0
    return (ts if ts else 2**63 - 1, -severity_rank(issue), str(issue.get("tag") or ""))


def incident_sort_key(group: IncidentGroup) -> tuple[int, int]:
    severity = SEVERITY_RANK.get(str(group.max_severity or "low"), 0)
    return (group.start_ts if group.start_ts else 2**63 - 1, -severity)


def issue_time_bounds(issue: dict[str, Any]) -> tuple[int, int]:
    first = as_millis(issue.get("first_seen_ts"))
    last = as_millis(issue.get("last_seen_ts"))
    if first and last:
        return min(first, last), max(first, last)
    value = first or last
    return value, value


def issue_family(issue: dict[str, Any]) -> str:
    category = str(issue.get("category") or "").lower()
    tag = str(issue.get("tag") or "").lower()
    if category == "moderation" or tag in MODERATION_TAGS:
        return "moderation"
    if tag == "feature_broken" and looks_like_abuse(issue):
        return "moderation"
    if category == "suggestion" or tag in SUGGESTION_TAGS:
        return "suggestion"
    return "operations"


def looks_like_abuse(issue: dict[str, Any]) -> bool:
    text_parts = [
        str(issue.get("tag") or ""),
        str(issue.get("title") or ""),
        " ".join(str(term) for term in issue.get("dialogue_terms") or []),
        " ".join(str(sample) for sample in issue.get("evidence_samples") or []),
    ]
    text = " ".join(text_parts).lower()
    return any(hint.lower() in text for hint in ABUSE_HINTS)


def issue_scopes(issue: dict[str, Any]) -> list[str]:
    scopes: list[str] = []
    for value in issue.get("affected_servers") or []:
        append_scope(scopes, str(value))
    for value in issue.get("affected_locations") or []:
        raw = str(value)
        append_scope(scopes, raw)
        server = re.split(r"[/@]", raw, maxsplit=1)[0]
        append_scope(scopes, server)
    return scopes or ["__window__"]


def append_scope(scopes: list[str], value: str):
    value = value.strip()
    if value and value not in scopes:
        scopes.append(value)


def severity_rank(issue: dict[str, Any]) -> int:
    return SEVERITY_RANK.get(str(issue.get("severity") or "low").lower(), 0)


def as_millis(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
