"""Application-level presentation model for MineSentinel reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .incidents import IncidentGroup, IncidentGrouper, IssuePolicy


@dataclass(frozen=True)
class ReportPresentation:
    """Normalized view model consumed by report renderers."""

    report: dict[str, Any]
    categories: dict[str, Any]
    issues: list[dict[str, Any]]
    actionable_issues: list[dict[str, Any]]
    incidents: list[IncidentGroup]
    total_count: int
    dedupe_count: int
    unique_players: int


class ReportPresentationBuilder:
    """Build a renderer-friendly view model from raw report facts."""

    def __init__(
        self,
        issue_policy: IssuePolicy | None = None,
        incident_grouper: IncidentGrouper | None = None,
    ):
        self.issue_policy = issue_policy or IssuePolicy()
        self.incident_grouper = incident_grouper or IncidentGrouper()

    def build(
        self,
        report: dict,
        total_count: int,
        dedupe_count: int,
        unique_players: int,
    ) -> ReportPresentation:
        categories = report.get("categories") or {}
        issues = [issue for issue in report.get("issues") or [] if isinstance(issue, dict)]
        actionable = self.issue_policy.actionable_issues(issues)
        incidents = self.incident_grouper.group(actionable)
        return ReportPresentation(
            report=report,
            categories=categories,
            issues=issues,
            actionable_issues=actionable,
            incidents=incidents,
            total_count=total_count,
            dedupe_count=dedupe_count,
            unique_players=unique_players,
        )
