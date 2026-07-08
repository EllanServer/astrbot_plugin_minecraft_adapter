"""Dialogue signal aggregation for player-chat issue detection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..models import ObservationRecord
from .common import record_location
from .dialogue_rules import DialogueRule
from .dialogue_terms import matched_terms, message_fingerprint
from .player_refs import mentioned_players, record_player


@dataclass
class DialogueSignalGroup:
    rule: DialogueRule | None = None
    incident_index: int = 0
    records: list[ObservationRecord] = field(default_factory=list)
    context_samples: list[str] = field(default_factory=list)
    terms: Counter = field(default_factory=Counter)
    urgent: int = 0
    evidence_count: int = 0
    message_fingerprints: set[str] = field(default_factory=set)
    signal_fingerprints: set[str] = field(default_factory=set)
    urgent_signal_fingerprints: set[str] = field(default_factory=set)
    players: set[str] = field(default_factory=set)
    mentioned_players: set[str] = field(default_factory=set)
    servers: set[str] = field(default_factory=set)
    backends: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    first_ts: int = 0
    last_ts: int = 0

    @property
    def signal_count(self) -> int:
        return len(self.signal_fingerprints)

    @property
    def distinct_message_count(self) -> int:
        return len(self.message_fingerprints)

    @property
    def urgent_signal_count(self) -> int:
        return len(self.urgent_signal_fingerprints)


class DialogueSignalCollector:
    """Aggregates matched chat records into bounded issue signals."""

    def __init__(self, max_issue_records: int, incident_gap_seconds: int = 0):
        self.max_issue_records = max_issue_records
        self.incident_gap_ms = max(0, int(incident_gap_seconds)) * 1000
        self._groups: dict[tuple[str, str, int], DialogueSignalGroup] = {}
        self._active_group_keys: dict[tuple[str, str], tuple[str, str, int]] = {}
        self._incident_counters: Counter = Counter()

    def add(
        self,
        record: ObservationRecord,
        rule: DialogueRule,
        terms: list[str],
        normalized_text: str,
        urgent_terms: list[str] | None = None,
    ):
        group = self._group_for(record, rule)
        group.rule = rule
        group.evidence_count += 1
        self._add_evidence_sample(group.records, record)
        group.terms.update(terms)

        player = record_player(record)
        message_key = message_fingerprint(normalized_text)
        fingerprint = _signal_fingerprint(player, message_key)
        group.signal_fingerprints.add(fingerprint)
        group.message_fingerprints.add(message_key)
        if player:
            group.players.add(player)
        group.mentioned_players.update(mentioned_players(record.content, player))
        if record.server_id:
            group.servers.add(record.server_id)
        if record.backend_server:
            group.backends.add(record.backend_server)
        location = record_location(record)
        if location:
            group.locations.add(location)
        if not group.first_ts or record.timestamp < group.first_ts:
            group.first_ts = record.timestamp
        if record.timestamp > group.last_ts:
            group.last_ts = record.timestamp
        if urgent_terms is None:
            urgent_terms = matched_terms(normalized_text, rule.urgent_terms)
        if urgent_terms:
            group.urgent += 1
            group.urgent_signal_fingerprints.add(fingerprint)

    def groups(self) -> list[DialogueSignalGroup]:
        return list(self._groups.values())

    def _group_for(
        self,
        record: ObservationRecord,
        rule: DialogueRule,
    ) -> DialogueSignalGroup:
        base_key = (rule.category, rule.tag)
        active_key = self._active_group_keys.get(base_key)
        if active_key and not self._starts_new_incident(record, self._groups[active_key]):
            return self._groups[active_key]

        incident_index = self._incident_counters[base_key]
        self._incident_counters[base_key] += 1
        group_key = (rule.category, rule.tag, incident_index)
        group = DialogueSignalGroup(rule=rule, incident_index=incident_index)
        self._groups[group_key] = group
        self._active_group_keys[base_key] = group_key
        return group

    def _starts_new_incident(
        self,
        record: ObservationRecord,
        group: DialogueSignalGroup,
    ) -> bool:
        if not self.incident_gap_ms or not record.timestamp or not group.last_ts:
            return False
        return record.timestamp - group.last_ts > self.incident_gap_ms

    def _add_evidence_sample(
        self,
        samples: list[ObservationRecord],
        record: ObservationRecord,
    ):
        if self.max_issue_records <= 0:
            return
        if len(samples) < self.max_issue_records:
            samples.append(record)
            return

        player = record_player(record)
        sample_players = [record_player(item) for item in samples]
        if player and player not in sample_players:
            replace_index = replaceable_sample_index(sample_players)
            samples[replace_index] = record
            return

        if record.timestamp >= samples[-1].timestamp:
            samples[-1] = record


def replaceable_sample_index(sample_players: list[str]) -> int:
    counts = Counter(player for player in sample_players if player)
    for index in range(len(sample_players) - 1, -1, -1):
        player = sample_players[index]
        if player and counts[player] > 1:
            return index
    return max(0, len(sample_players) - 1)


def signal_fingerprint(record: ObservationRecord, normalized_text: str) -> str:
    return _signal_fingerprint(
        record_player(record),
        message_fingerprint(normalized_text),
    )


def _signal_fingerprint(player: str, message_key: str) -> str:
    return f"{player.lower()}|{message_key}"
