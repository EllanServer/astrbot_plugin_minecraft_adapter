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
            yield event.plain_result("用法: /mc monitor status")
            return
        if not self.service:
            yield event.plain_result("MineSentinel 未初始化")
            return
        yield event.plain_result(self.service.monitor_status())

    async def handle_report(self, event: "AstrMessageEvent", args: str = ""):
        arg_text = str(args or "").strip()
        subcommand, _, rest = arg_text.partition(" ")
        subcommand = subcommand.lower()
        if subcommand not in {"now", "log"}:
            yield event.plain_result(_report_usage())
            return
        if not self.service:
            yield event.plain_result("MineSentinel 未初始化")
            return

        if subcommand == "log":
            target = parse_log_report_args(rest)
            if not target.source:
                yield event.plain_result(_report_usage())
                return
            result = await self.service.report_console_log_result(
                current_session=event.unified_msg_origin,
                source=target.source,
                server_id=target.server_id,
            )
        else:
            target = parse_report_args(rest.split())
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


class LogReportTarget:
    def __init__(self, source: str = "", server_id: str | None = None):
        self.source = source
        self.server_id = server_id


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


def parse_log_report_args(text: str) -> LogReportTarget:
    source = str(text or "").strip()
    if not source:
        return LogReportTarget()

    first, has_space, rest = source.partition(" ")
    if has_space and first.lower().startswith("server="):
        server_id = first.split("=", 1)[1].strip() or None
        return LogReportTarget(source=rest.strip(), server_id=server_id)

    if has_space and _looks_like_server_id(first) and _looks_like_log_source(rest):
        return LogReportTarget(source=rest.strip(), server_id=first)

    return LogReportTarget(source=source)


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


def _looks_like_server_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value or ""))


def _looks_like_log_source(value: str) -> bool:
    text = str(value or "").lstrip()
    first_token = text.split(maxsplit=1)[0] if text else ""
    return (
        text.startswith("[")
        or first_token.startswith("https://mclo.gs/")
        or first_token.startswith("https://api.mclo.gs/")
    )


def _report_usage() -> str:
    return (
        "用法: /mc report now [服务器ID] [8h]\n"
        "或: /mc report log [服务器ID|server=服务器ID] <mclo.gs链接|控制台日志文本>"
    )


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
