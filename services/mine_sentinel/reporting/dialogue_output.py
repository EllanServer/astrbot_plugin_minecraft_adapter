"""Build report-ready issues from aggregated dialogue signals."""

from __future__ import annotations

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
from .dialogue_rules import DialogueRule
from .dialogue_scoring import dialogue_score, dialogue_severity, should_alert
from .dialogue_signals import DialogueSignalGroup


class DialogueIssueBuilder:
    """Scores dialogue signal groups and formats them for reports."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config

    def build(self, groups: list[DialogueSignalGroup]) -> dict[str, Any]:
        signals = self.signals(groups)
        return {
            "findings": [self.finding_line(signal) for signal in signals],
            "issues": [self.issue(signal) for signal in signals],
            "category_lines": self.category_lines(signals),
        }

    def signals(self, groups: list[DialogueSignalGroup]) -> list[dict[str, Any]]:
        signals = []
        for group in groups:
            signal = self.signal(group)
            if signal:
                signals.append(signal)
        signals.sort(
            key=lambda item: (
                SEVERITY_RANK.get(item["severity"], 0),
                item["score"],
                item["signal_count"],
            ),
            reverse=True,
        )
        return signals[: self.config.dialogue.max_findings]

    def signal(self, group: DialogueSignalGroup) -> dict[str, Any] | None:
        rule = group.rule
        if rule is None:
            return None
        evidence_count = group.evidence_count
        if evidence_count < self.config.dialogue.min_evidence_count:
            return None
        players = sorted(group.players)
        signal_count = group.signal_count
        distinct_message_count = group.distinct_message_count
        urgent_signal_count = group.urgent_signal_count
        affected_servers = sorted(group.servers)
        affected_backends = sorted(group.backends)
        affected_locations = sorted(group.locations)
        score = dialogue_score(
            signal_count,
            len(players),
            urgent_signal_count,
            len(affected_locations),
            len(affected_servers),
        )
        if score < self.config.dialogue.min_issue_score:
            return None
        return self._signal_dict(
            rule,
            group.records,
            group.terms,
            group.urgent,
            score,
            evidence_count,
            signal_count,
            distinct_message_count,
            players,
            sorted(group.mentioned_players),
            affected_servers,
            affected_backends,
            affected_locations,
            group.first_ts,
            group.last_ts,
            urgent_signal_count,
            group.incident_index,
            group.context_samples,
        )

    def _signal_dict(
        self,
        rule: DialogueRule,
        records: list[ObservationRecord],
        terms,
        urgent_count: int,
        score: float,
        evidence_count: int,
        signal_count: int,
        distinct_message_count: int,
        players: list[str],
        mentioned_players: list[str],
        affected_servers: list[str],
        affected_backends: list[str],
        affected_locations: list[str],
        first_ts: int,
        last_ts: int,
        urgent_signal_count: int,
        incident_index: int,
        context_samples: list[str],
    ) -> dict[str, Any]:
        if not players:
            players = player_name_list(records)
        if not affected_locations:
            affected_locations = location_list(records)
        severity = dialogue_severity(
            rule,
            signal_count,
            len(players),
            urgent_signal_count,
            len(affected_locations),
            len(affected_servers),
        )
        return {
            "category": rule.category,
            "tag": rule.tag,
            "incident_index": incident_index,
            "title": rule.title,
            "severity": severity,
            "score": round(score, 2),
            "affected_servers": affected_servers,
            "affected_backends": affected_backends,
            "affected_locations": affected_locations,
            "affected_locations_text": format_locations(affected_locations),
            "evidence_count": evidence_count,
            "signal_count": signal_count,
            "distinct_message_count": distinct_message_count,
            "unique_players": len(players),
            "players": players,
            "players_text": format_players(players),
            "mentioned_players": mentioned_players,
            "mentioned_players_text": format_players(mentioned_players),
            "top_terms": [term for term, _ in terms.most_common(6)],
            "urgent_count": urgent_count,
            "urgent_signal_count": urgent_signal_count,
            "first_seen_ts": first_ts,
            "last_seen_ts": last_ts,
            "records": records,
            "context_samples": context_samples,
            "suggested_action": rule.suggested_action,
            "should_alert": should_alert(
                self.config,
                severity,
                signal_count,
                len(players),
            ),
        }

    def issue(self, signal: dict[str, Any]) -> dict[str, Any]:
        samples = self._evidence_samples(signal)
        return {
            "category": signal["category"],
            "tag": signal["tag"],
            "source_tag": f"dialogue:{signal['tag']}",
            "incident_index": signal["incident_index"],
            "severity": signal["severity"],
            "score": signal["score"],
            "confidence": min(0.98, 0.45 + signal["score"] * 0.08),
            "affected_servers": signal["affected_servers"],
            "affected_backends": signal["affected_backends"],
            "affected_locations": signal["affected_locations"],
            "affected_locations_text": signal["affected_locations_text"],
            "evidence_count": signal["evidence_count"],
            "signal_count": signal["signal_count"],
            "distinct_message_count": signal["distinct_message_count"],
            "unique_players": signal["unique_players"],
            "players": signal["players"],
            "players_text": signal["players_text"],
            "mentioned_players": signal["mentioned_players"],
            "mentioned_players_text": signal["mentioned_players_text"],
            "dialogue_terms": signal["top_terms"],
            "urgent_signal_count": signal["urgent_signal_count"],
            "first_seen_ts": signal["first_seen_ts"],
            "last_seen_ts": signal["last_seen_ts"],
            "evidence_samples": (
                samples if self.config.report.include_evidence_samples else []
            ),
            "suggested_action": signal["suggested_action"],
            "should_alert": signal["should_alert"],
        }

    def _evidence_samples(self, signal: dict[str, Any]) -> list[str]:
        context_samples = signal.get("context_samples") or []
        if context_samples:
            return [
                str(sample)
                for sample in context_samples[: self.config.report.max_evidence_samples]
            ]
        return [
            record.evidence_text()
            for record in signal["records"][: self.config.report.max_evidence_samples]
        ]

    def finding_line(self, signal: dict[str, Any]) -> str:
        terms = "、".join(signal["top_terms"]) or "无"
        mentioned = ""
        if signal["mentioned_players"]:
            mentioned = f"，提到 {signal['mentioned_players_text']}"
        signal_hint = ""
        if signal["signal_count"] != signal["evidence_count"]:
            signal_hint = f"（{signal['signal_count']} 个去重信号）"
        location_hint = ""
        if signal["affected_locations"]:
            location_hint = f"，位置 {signal['affected_locations_text']}"
        return (
            f"[{signal['severity']}] {signal['title']}: "
            f"{signal['evidence_count']} 条聊天{signal_hint}，"
            f"玩家 {signal['players_text']}，"
            f"关键词 {terms}{mentioned}{location_hint}。"
        )

    def category_lines(self, signals: list[dict[str, Any]]) -> dict[str, list[str]]:
        lines: dict[str, list[str]] = defaultdict(list)
        for signal in signals:
            lines[signal["category"]].append(self.finding_line(signal))
        return dict(lines)
