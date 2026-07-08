"""MineSentinel report orchestration."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from typing import Any

from ..console_log import console_log_window_minutes, parse_minecraft_console_log
from ..models import MineSentinelConfig, ObservationRecord
from .ai_summary import AIReportSummarizer
from .rules import HeuristicReportBuilder


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
        ai_report = await self.ai.build(records, window_minutes, heuristic, umo)
        return ai_report or heuristic

    async def build_console_log_report(
        self,
        lines: str | Iterable[str],
        *,
        server_id: str = "minecraft",
        server_name: str = "",
        base_date: _dt.date | None = None,
        window_minutes: int | None = None,
        umo: str | None = None,
    ) -> tuple[dict[str, Any], list[ObservationRecord]]:
        """Parse a raw Minecraft console log and build the same admin report."""

        records = parse_minecraft_console_log(
            lines,
            server_id=server_id,
            server_name=server_name,
            base_date=base_date,
        )
        window = window_minutes or console_log_window_minutes(records)
        return await self.build_report(records, window, server_id, umo), records

    def build_heuristic_report(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        server_id: str | None = None,
    ) -> dict[str, Any]:
        return self.rules.build(records, window_minutes, server_id)
