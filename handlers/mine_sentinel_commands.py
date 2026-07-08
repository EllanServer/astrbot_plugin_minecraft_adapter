"""MineSentinel command group handlers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ..services.mine_sentinel import MineSentinelService


class MineSentinelCommandHandler:
    def __init__(self, service: "MineSentinelService | None" = None):
        self.service = service

    async def handle_monitor(self, event: "AstrMessageEvent", args: str = ""):
        tokens = str(args or "").strip().split()
        if not tokens or tokens[0].lower() != "status":
            yield event.plain_result("用法: /ms monitor status")
            return
        if not self.service:
            yield event.plain_result("MineSentinel 未初始化")
            return
        yield event.plain_result(self.service.monitor_status())

    async def handle_report(self, event: "AstrMessageEvent", args: str = ""):
        tokens = str(args or "").strip().split()
        if not tokens or tokens[0].lower() != "now":
            yield event.plain_result("用法: /ms report now [服务器ID] [8h]")
            return
        if not self.service:
            yield event.plain_result("MineSentinel 未初始化")
            return

        target = parse_report_args(tokens[1:])
        result = await self.service.report_now_result(
            current_session=event.unified_msg_origin,
            server_id=target.server_id,
            window_minutes=target.window_minutes,
        )
        yield _event_report_result(event, result)


class ReportTarget:
    def __init__(self, server_id: str | None = None, window_minutes: int | None = None):
        self.server_id = server_id
        self.window_minutes = window_minutes


def parse_report_args(tokens: list[str]) -> ReportTarget:
    server_id = None
    window_minutes = None
    for token in tokens:
        parsed_window = parse_window_minutes(token)
        if parsed_window is not None:
            window_minutes = parsed_window
        elif server_id is None:
            server_id = token
    return ReportTarget(server_id=server_id, window_minutes=window_minutes)


def parse_window_minutes(value: str) -> int | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    match = re.fullmatch(r"(\d+)(m|min|分钟|h|小时)?", text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2) or "m"
    if unit in {"h", "小时"}:
        return max(1, amount * 60)
    return max(1, amount)


def _event_report_result(event: "AstrMessageEvent", result):
    image = getattr(result, "image", None)
    if image is not None:
        try:
            from astrbot.api.message_components import Image

            chain_result = getattr(event, "chain_result", None)
            if callable(chain_result):
                return chain_result([Image.fromBytes(image.getvalue())])
        except Exception:
            pass
    return event.plain_result(str(result))
