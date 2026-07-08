"""Term normalization and matching helpers for dialogue analysis."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dialogue_rules import DialogueRule


NEGATION_PREFIXES = (
    "不",
    "没",
    "没有",
    "不是",
    "并不",
    "不太",
)

REPEATED_CHAR_RE = re.compile(r"(.)\1{2,}")


def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def message_fingerprint(text: str) -> str:
    normalized = normalize_text(text)
    compact = "".join(ch for ch in normalized if ch.isalnum())
    return REPEATED_CHAR_RE.sub(r"\1\1", compact)


def matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    matched = []
    for term in terms:
        normalized = term.lower()
        if normalized in text and not term_is_negated(text, normalized):
            matched.append(term)
    return matched


def term_is_negated(text: str, term: str) -> bool:
    start = 0
    saw_negated = False
    while True:
        index = text.find(term, start)
        if index < 0:
            return saw_negated
        prefix_window = text[max(0, index - 4) : index]
        if any(prefix_window.endswith(prefix) for prefix in NEGATION_PREFIXES):
            saw_negated = True
            start = index + len(term)
            continue
        return False


class DialogueRuleMatcher:
    """Indexes rule terms so each normalized chat line is scanned once."""

    def __init__(self, rules: tuple["DialogueRule", ...]):
        self.rules = rules
        self._keyword_terms = self._index_rule_terms("keywords")
        self._keyword_terms_by_initial = self._bucket_rule_terms(self._keyword_terms)
        self._urgent_terms = self._index_terms_by_rule("urgent_terms")

    def direct_matches(
        self,
        normalized_text: str,
    ) -> list[tuple["DialogueRule", list[str]]]:
        matches: dict["DialogueRule", list[str]] = defaultdict(list)
        cache: dict[str, bool] = {}
        for _sequence, normalized, original, rule in self._candidate_rule_terms(
            normalized_text
        ):
            if self._term_matches(normalized_text, normalized, cache):
                matches[rule].append(original)
        return list(matches.items())

    def urgent_terms(
        self,
        normalized_text: str,
        rule: "DialogueRule",
    ) -> list[str]:
        cache: dict[str, bool] = {}
        return [
            original
            for normalized, original in self._urgent_terms.get(rule, ())
            if self._term_matches(normalized_text, normalized, cache)
        ]

    def matched_rule(self, normalized_text: str) -> "DialogueRule | None":
        best_rule = None
        best_count = 0
        for rule, terms in self.direct_matches(normalized_text):
            count = len(terms)
            if count > best_count or (
                count and count == best_count and _rule_priority(rule) < _rule_priority(best_rule)
            ):
                best_rule = rule
                best_count = count
        return best_rule

    def _index_rule_terms(
        self,
        field_name: str,
    ) -> tuple[tuple[int, str, str, "DialogueRule"], ...]:
        indexed = []
        sequence = 0
        for rule in self.rules:
            for original in getattr(rule, field_name):
                normalized = str(original or "").lower()
                if normalized:
                    indexed.append((sequence, normalized, original, rule))
                    sequence += 1
        return tuple(indexed)

    @staticmethod
    def _bucket_rule_terms(
        terms: tuple[tuple[int, str, str, "DialogueRule"], ...],
    ) -> dict[str, tuple[tuple[int, str, str, "DialogueRule"], ...]]:
        buckets: dict[str, list[tuple[int, str, str, "DialogueRule"]]] = defaultdict(list)
        for item in terms:
            _sequence, normalized, _original, _rule = item
            buckets[normalized[0]].append(item)
        return {key: tuple(value) for key, value in buckets.items()}

    def _candidate_rule_terms(
        self,
        text: str,
    ) -> list[tuple[int, str, str, "DialogueRule"]]:
        seen_initials: set[str] = set()
        seen_sequences: set[int] = set()
        sparse_limit = max(1, len(self._keyword_terms) // 4)
        candidates: list[tuple[int, str, str, "DialogueRule"]] | None = []
        for char in text:
            if char in seen_initials:
                continue
            seen_initials.add(char)
            bucket = self._keyword_terms_by_initial.get(char, ())
            if not bucket:
                continue
            if candidates is not None and len(candidates) + len(bucket) > sparse_limit:
                candidates = None
            if candidates is None:
                for item in bucket:
                    seen_sequences.add(item[0])
            else:
                for item in bucket:
                    seen_sequences.add(item[0])
                    candidates.append(item)
        if not seen_sequences:
            return []
        if candidates is not None:
            candidates.sort(key=lambda item: item[0])
            return candidates
        return [
            item
            for item in self._keyword_terms
            if item[0] in seen_sequences
        ]

    def _index_terms_by_rule(
        self,
        field_name: str,
    ) -> dict["DialogueRule", tuple[tuple[str, str], ...]]:
        indexed = {}
        for rule in self.rules:
            terms = []
            for original in getattr(rule, field_name):
                normalized = str(original or "").lower()
                if normalized:
                    terms.append((normalized, original))
            indexed[rule] = tuple(terms)
        return indexed

    @staticmethod
    def _term_matches(
        text: str,
        term: str,
        cache: dict[str, bool],
    ) -> bool:
        cached = cache.get(term)
        if cached is not None:
            return cached
        matched = term in text and not term_is_negated(text, term)
        cache[term] = matched
        return matched


def _rule_priority(rule: "DialogueRule | None") -> int:
    if rule is None:
        return 100
    if rule.category == "moderation":
        return 0
    return 10
