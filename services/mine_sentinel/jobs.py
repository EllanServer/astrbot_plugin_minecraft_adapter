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


class HourlySummaryJob:
    """Runs once per hour, on the hour, to summarize the past hour of logs.

    After `hours_per_cycle` summaries have been collected, integrates them
    into a single cycle report and delivers it.
    """

    def __init__(
        self,
        config: MineSentinelConfig,
        run_hour: Callable[[int, int, str], Awaitable[None]],
    ):
        self.config = config
        self.run_hour = run_hour
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

    @staticmethod
    def seconds_until_next_hour(now: float | None = None) -> float:
        now = now or time.time()
        # Align to wall-clock hour boundary in local time.
        return 3600.0 - (now % 3600.0)

    async def _loop(self):
        # On startup, immediately process the current partial hour.
        try:
            await self._process_current_partial_hour()
        except Exception as exc:
            logger.error(f"[MineSentinel] hourly 启动补读失败: {exc}")

        while True:
            try:
                sleep_seconds = self.seconds_until_next_hour()
                await asyncio.sleep(sleep_seconds)
                await self._process_last_full_hour()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"[MineSentinel] hourly 任务失败: {exc}")
                await asyncio.sleep(60)

    async def _process_last_full_hour(self):
        now_ms = int(time.time() * 1000)
        # The hour that just completed: [floor(now-3600s), floor(now)) in ms.
        current_hour_start_ms = (now_ms // 3600_000) * 3600_000
        last_hour_start_ms = current_hour_start_ms - 3_600_000
        last_hour_end_ms = current_hour_start_ms
        for source in self._enabled_sources():
            try:
                await self.run_hour(last_hour_start_ms, last_hour_end_ms, source.server_id)
            except Exception as exc:
                logger.error(
                    f"[MineSentinel] hourly 处理 {source.server_id} "
                    f"({last_hour_start_ms}-{last_hour_end_ms}) 失败: {exc}"
                )

    async def _process_current_partial_hour(self):
        now_ms = int(time.time() * 1000)
        current_hour_start_ms = (now_ms // 3600_000) * 3600_000
        # Skip if we are within the first 60s of the hour (almost nothing to read).
        if now_ms - current_hour_start_ms < 60_000:
            logger.info(
                "[MineSentinel] hourly 启动补读：当前小时刚开始，跳过补读，"
                "等待下个整点处理完整小时"
            )
            return
        for source in self._enabled_sources():
            try:
                await self.run_hour(
                    current_hour_start_ms, now_ms, source.server_id
                )
            except Exception as exc:
                logger.error(
                    f"[MineSentinel] hourly 启动补读 {source.server_id} 失败: {exc}"
                )

    def _enabled_sources(self):
        return [
            s
            for s in self.config.runtime_log.sources
            if s.enabled and (s.root or s.logs_dir or s.log_file)
        ]
