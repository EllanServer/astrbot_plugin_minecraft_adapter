"""Term normalization and matching helpers for dialogue analysis.

Performance-critical module: every CHAT observation runs through
``RuleTermMatcher.scan`` twice (window sampling + report building). When
the ``mine_sentinel_rs`` Rust extension is importable, all hot functions
delegate to it; otherwise we fall back to the pure-Python implementation
so the plugin still works without a Rust toolchain installed.
"""

from __future__ import annotations

import re
from typing import Iterable

try:
    # Native Rust core (PyO3). Built by `maturin build --release`; absent
    # in dev environments without a Rust toolchain — graceful fallback below.
    from mine_sentinel_rs import (  # type: ignore[import-not-found]
        RuleTermMatcher as _RsRuleTermMatcher,
        matched_terms as _rs_matched_terms,
        message_fingerprint_py as _rs_message_fingerprint,
        normalize_text_py as _rs_normalize_text,
        term_is_negated_py as _rs_term_is_negated,
    )

    _HAS_RUST = True
except ImportError:  # pragma: no cover - exercised when Rust extension absent
    _HAS_RUST = False


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
    if _HAS_RUST:
        return _rs_normalize_text(text)
    return " ".join((text or "").lower().split())


def message_fingerprint(text: str) -> str:
    if _HAS_RUST:
        return _rs_message_fingerprint(text)
    normalized = normalize_text(text)
    compact = "".join(ch for ch in normalized if ch.isalnum())
    return REPEATED_CHAR_RE.sub(r"\1\1", compact)


def matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    if _HAS_RUST:
        return _rs_matched_terms(text, terms)
    matched = []
    for term in terms:
        normalized = term.lower()
        if normalized in text and not term_is_negated(text, normalized):
            matched.append(term)
    return matched


def term_is_negated(text: str, term: str) -> bool:
    if _HAS_RUST:
        return _rs_term_is_negated(text, term)
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


class RuleTermMatcher:
    """Match many keyword rules against a text in a single pass.

    Each rule's keywords are compiled into one alternation regex so that a
    single ``re.finditer`` scan reports every hit and its position, instead of
    looping over rules × keywords with repeated ``str.find`` calls. Negation
    is still applied per hit via :func:`term_is_negated`.

    When the Rust extension ``mine_sentinel_rs`` is available, ``scan`` and
    ``matched_keywords`` delegate to it for ~10-20x throughput on the per-CHAT
    hot path. Otherwise falls back to the pure-Python regex implementation.
    """

    def __init__(self, rules: Iterable[tuple[object, tuple[str, ...], tuple[str, ...]]]):
        # rules: iterable of (rule_obj, keywords, urgent_terms)
        rules_list: list[tuple[object, tuple[str, ...], tuple[str, ...]]] = list(rules)
        self._rules = rules_list

        if _HAS_RUST:
            # Rust matcher keeps its own index; Python only holds the rule
            # objects for identity comparisons (Rust returns them as dict keys).
            self._rs = _RsRuleTermMatcher(rules_list)
        else:
            self._rs = None
            self._keyword_owners: dict[str, list[object]] = {}
            self._urgent_owners: dict[str, list[object]] = {}
            self._keyword_display: dict[str, str] = {}
            self._urgent_display: dict[str, str] = {}
            keyword_terms: set[str] = set()
            urgent_terms: set[str] = set()
            for rule, keywords, urgent in rules_list:
                for term in keywords:
                    lowered = term.lower()
                    keyword_terms.add(lowered)
                    self._keyword_owners.setdefault(lowered, []).append(rule)
                    self._keyword_display.setdefault(lowered, term)
                for term in urgent:
                    lowered = term.lower()
                    urgent_terms.add(lowered)
                    self._urgent_owners.setdefault(lowered, []).append(rule)
                    self._urgent_display.setdefault(lowered, term)
            self._keyword_re = _compile_term_pattern(keyword_terms)
            self._urgent_re = _compile_term_pattern(urgent_terms)

    def scan(
        self, text: str
    ) -> dict[object, tuple[list[str], list[str]]]:
        """Return {rule: (matched_keywords, matched_urgent_terms)} for the text."""
        if self._rs is not None:
            return self._rs.scan(text)
        result: dict[object, tuple[list[str], list[str]]] = {}
        if not text:
            return result

        keyword_hits = _collect_non_negated_hits(text, self._keyword_re)
        for lowered in keyword_hits:
            display = self._keyword_display.get(lowered, lowered)
            for rule in self._keyword_owners.get(lowered, ()):
                kw, ug = result.setdefault(rule, ([], []))
                kw.append(display)

        urgent_hits = _collect_non_negated_hits(text, self._urgent_re)
        for lowered in urgent_hits:
            display = self._urgent_display.get(lowered, lowered)
            for rule in self._urgent_owners.get(lowered, ()):
                kw, ug = result.setdefault(rule, ([], []))
                ug.append(display)
        return result

    def matched_keywords(self, text: str) -> dict[object, list[str]]:
        """Return {rule: matched_keywords} ignoring urgent terms."""
        if self._rs is not None:
            return self._rs.matched_keywords(text)
        hits = _collect_non_negated_hits(text, self._keyword_re)
        out: dict[object, list[str]] = {}
        for lowered in hits:
            display = self._keyword_display.get(lowered, lowered)
            for rule in self._keyword_owners.get(lowered, ()):
                out.setdefault(rule, []).append(display)
        return out


def _compile_term_pattern(terms: set[str]) -> re.Pattern[str]:
    if not terms:
        return re.compile(r"(?!)")
    # Sort by length desc so longer terms match first at a given position;
    # escape each term for regex safety.
    alternation = "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
    return re.compile(alternation)


def _collect_non_negated_hits(
    text: str, pattern: re.Pattern[str]
) -> dict[str, list[object]]:
    """Collect non-negated term hits keyed by the matched (lowered) term string.

    A term may appear multiple times; we keep one entry but remember it matched.
    Negated occurrences are ignored, matching :func:`matched_terms` semantics
    (a non-negated occurrence anywhere makes the term count).
    """
    hits: dict[str, list[object]] = {}
    for match in pattern.finditer(text):
        term = match.group(0)
        if term_is_negated(text, term):
            continue
        hits.setdefault(term, [])
    return hits
