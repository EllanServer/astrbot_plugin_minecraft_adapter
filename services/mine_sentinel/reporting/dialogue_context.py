"""Context-aware dialogue continuation detection."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ObservationRecord
from .common import record_location
from .dialogue_rules import DialogueRule


CONTINUATION_TERMS = (
    "+1",
    "我也是",
    "我也",
    "一样",
    "同样",
    "这边也是",
    "这里也是",
    "也这样",
    "也不行",
    "也有",
    "俺也一样",
)


@dataclass(frozen=True)
class DialogueContextMatch:
    rule: DialogueRule
    terms: list[str]


@dataclass(frozen=True)
class _DialogueAnchor:
    rule: DialogueRule
    timestamp: int


class DialogueContinuationTracker:
    """Tracks short follow-up messages that depend on nearby issue context."""

    def __init__(self, window_seconds: int, max_anchors_per_location: int = 4):
        self.window_ms = max(0, int(window_seconds)) * 1000
        self.max_anchors_per_location = max(1, int(max_anchors_per_location))
        self._anchors: dict[str, list[_DialogueAnchor]] = {}

    def remember(
        self,
        record: ObservationRecord,
        rule: DialogueRule,
    ):
        key = self._record_key(record)
        if not key:
            return
        anchors = self._anchors.setdefault(key, [])
        anchors.append(_DialogueAnchor(rule, record.timestamp))
        self._prune(key, record.timestamp)
        if len(anchors) > self.max_anchors_per_location:
            del anchors[: len(anchors) - self.max_anchors_per_location]

    def matches(
        self,
        record: ObservationRecord,
        normalized_text: str,
    ) -> list[DialogueContextMatch]:
        if not self.window_ms or not is_continuation_message(normalized_text):
            return []
        key = self._record_key(record)
        if not key:
            return []
        self._prune(key, record.timestamp)
        anchors = self._anchors.get(key, [])
        matches: list[DialogueContextMatch] = []
        seen_rules: set[str] = set()
        for anchor in reversed(anchors):
            if record.timestamp < anchor.timestamp:
                continue
            if record.timestamp - anchor.timestamp > self.window_ms:
                continue
            if anchor.rule.tag in seen_rules:
                continue
            seen_rules.add(anchor.rule.tag)
            matches.append(
                DialogueContextMatch(anchor.rule, ["跟进反馈"])
            )
        return matches

    def _prune(self, key: str, now_ms: int):
        if not self.window_ms:
            self._anchors.pop(key, None)
            return
        cutoff = now_ms - self.window_ms
        anchors = [
            anchor
            for anchor in self._anchors.get(key, [])
            if anchor.timestamp >= cutoff
        ]
        if anchors:
            self._anchors[key] = anchors
        else:
            self._anchors.pop(key, None)

    @staticmethod
    def _record_key(record: ObservationRecord) -> str:
        return record_location(record)


def is_continuation_message(normalized_text: str) -> bool:
    compact = "".join(ch for ch in normalized_text if not ch.isspace())
    if not compact:
        return False
    return any(term in compact for term in CONTINUATION_TERMS)
