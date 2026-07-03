"""Term normalization and matching helpers for dialogue analysis."""

from __future__ import annotations

import re


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
