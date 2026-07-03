"""MineSentinel report generation components."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "AIReportSummarizer",
    "DIALOGUE_RULES",
    "DialogueRule",
    "HeuristicReportBuilder",
    "MineSentinelReporter",
    "PlayerDialogueAnalyzer",
    "matched_terms",
    "mentioned_players",
    "normalize_text",
    "record_player",
    "even_sample",
    "sample_records_for_ai",
    "term_is_negated",
]

if TYPE_CHECKING:
    from .ai_summary import AIReportSummarizer
    from .dialogue import PlayerDialogueAnalyzer
    from .dialogue_rules import DIALOGUE_RULES, DialogueRule
    from .dialogue_terms import matched_terms, normalize_text, term_is_negated
    from .player_refs import mentioned_players, record_player
    from .reporter import MineSentinelReporter
    from .rules import HeuristicReportBuilder
    from .sampling import even_sample, sample_records_for_ai


def __getattr__(name: str):
    if name == "AIReportSummarizer":
        from .ai_summary import AIReportSummarizer

        return AIReportSummarizer
    if name == "HeuristicReportBuilder":
        from .rules import HeuristicReportBuilder

        return HeuristicReportBuilder
    if name == "MineSentinelReporter":
        from .reporter import MineSentinelReporter

        return MineSentinelReporter
    if name == "PlayerDialogueAnalyzer":
        from .dialogue import PlayerDialogueAnalyzer

        return PlayerDialogueAnalyzer
    if name in {"DIALOGUE_RULES", "DialogueRule"}:
        from .dialogue_rules import DIALOGUE_RULES, DialogueRule

        return {"DIALOGUE_RULES": DIALOGUE_RULES, "DialogueRule": DialogueRule}[name]
    if name in {"matched_terms", "normalize_text", "term_is_negated"}:
        from .dialogue_terms import matched_terms, normalize_text, term_is_negated

        return {
            "matched_terms": matched_terms,
            "normalize_text": normalize_text,
            "term_is_negated": term_is_negated,
        }[name]
    if name in {"mentioned_players", "record_player"}:
        from .player_refs import mentioned_players, record_player

        return {"mentioned_players": mentioned_players, "record_player": record_player}[
            name
        ]
    if name in {"even_sample", "sample_records_for_ai"}:
        from .sampling import even_sample, sample_records_for_ai

        return {
            "even_sample": even_sample,
            "sample_records_for_ai": sample_records_for_ai,
        }[name]
    raise AttributeError(name)
