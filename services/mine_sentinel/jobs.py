"""MineSentinel background job helpers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from astrbot.api import logger

from .models import MineSentinelConfig


class PeriodicReportJob:
    def __init__(
        self,
        config: MineSentinelConfig,
        run_once: Callable[[], Awaitable[bool]],
        get_last_report_time: Callable[[], float],
    ):
        self.config = config
        self.run_once = run_once
        self.get_last_report_time = get_last_report_time
        self._task: asyncio.Task | None = None

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self):
        interval = max(1, self.config.report.interval_minutes) * 60
        cooldown = max(0, self.config.report.cooldown_seconds)
        while True:
            await asyncio.sleep(interval)
            if time.time() - self.get_last_report_time() < cooldown:
                continue
            try:
                await self.run_once()
            except Exception as exc:
                logger.error(f"[MineSentinel] 定时报告任务失败: {exc}")
