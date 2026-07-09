"""MineSentinel report orchestration."""

from __future__ import annotations

import logging
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .ai_summary import AIReportSummarizer
from .rules import HeuristicReportBuilder

_logger = logging.getLogger(__name__)


class MineSentinelReporter:
    """Builds rule-based reports and lets AI polish them when available."""

    def __init__(self, config: MineSentinelConfig, context: Any | None = None):
        self.rules = HeuristicReportBuilder(config)
        self.ai = AIReportSummarizer(config, context)

    async def build_report(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
        umo: str | None = None,
    ) -> dict[str, Any]:
        heuristic = self.rules.build(records, window_minutes, server_id)
        ai_records = self.rules.filter_records_for_report(records)
        try:
            ai_report = await self.ai.build(
                ai_records,
                window_minutes,
                heuristic,
                umo,
                review_records=records,
            )
        except Exception as exc:
            # AI 调用异常时回退到启发式报告，避免丢弃已构建的 heuristic。
            ai_report = None
            _logger.warning(f"[MineSentinel] AI 报告构建失败，回退启发式: {exc}")
        return ai_report or heuristic

    def build_heuristic_report(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
    ) -> dict[str, Any]:
        return self.rules.build(records, window_minutes, server_id)
