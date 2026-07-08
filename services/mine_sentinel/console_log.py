"""Parse Minecraft console logs into MineSentinel observations."""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterable

from .models import ObservationRecord


LOG_LINE_RE = re.compile(
    r"^\[(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\] "
    r"\[(?P<thread>[^/\]]+)/(?P<level>[A-Z]+)\]: (?P<message>.*)$"
)
COMMAND_RE = re.compile(r"^(?P<player>[A-Za-z0-9_]{1,16}) issued server command: (?P<command>/.*)$")
JOIN_RE = re.compile(r"^(?P<player>[A-Za-z0-9_]{1,16}) joined the game$")
QUIT_RE = re.compile(r"^(?P<player>[A-Za-z0-9_]{1,16}) left the game$")
LOST_RE = re.compile(
    r"^(?P<player>[A-Za-z0-9_]{1,16})(?: \([^)]*\))? lost connection: (?P<reason>.*)$"
)
CHAT_RE = re.compile(r"^<(?P<player>[^>]{1,32})> (?P<message>.+)$")
DONE_RE = re.compile(r"^Done \((?P<seconds>\d+(?:\.\d+)?)s\)!")
PLUGIN_RE = re.compile(r"^\[(?P<plugin>[^\]]+)\]\s*(?P<body>.*)$")


def parse_minecraft_console_log(
    lines: str | Iterable[str],
    *,
    server_id: str = "minecraft",
    server_name: str = "",
    base_date: _dt.date | None = None,
) -> list[ObservationRecord]:
    """Parse useful Paper/Leaf console lines for five-section admin reports."""

    if isinstance(lines, str):
        raw_lines = lines.splitlines()
    else:
        raw_lines = list(lines)

    base_date = base_date or _dt.date.today()
    records: list[ObservationRecord] = []
    day_offset = 0
    last_second_of_day = -1

    for line_number, raw_line in enumerate(raw_lines, 1):
        match = LOG_LINE_RE.match(raw_line.rstrip("\n"))
        if not match:
            continue
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        second = int(match.group("second"))
        second_of_day = hour * 3600 + minute * 60 + second
        if last_second_of_day >= 0 and second_of_day < last_second_of_day:
            day_offset += 1
        last_second_of_day = second_of_day
        timestamp = _timestamp_ms(base_date, day_offset, hour, minute, second, line_number)

        level = match.group("level")
        thread = match.group("thread")
        message = match.group("message").strip()
        if not message:
            continue

        record = _record_from_message(
            message,
            level=level,
            thread=thread,
            timestamp=timestamp,
            line_number=line_number,
            raw_line=raw_line.rstrip("\n"),
            server_id=server_id,
            server_name=server_name,
        )
        if record is not None:
            records.append(record)
    return records


def console_log_window_minutes(records: list[ObservationRecord]) -> int:
    """Infer a compact reporting window from parsed console observation times."""

    timestamps = [record.timestamp for record in records if record.timestamp > 0]
    if len(timestamps) < 2:
        return 1
    span_ms = max(timestamps) - min(timestamps)
    return max(1, int((span_ms + 59999) // 60000))


def _record_from_message(
    message: str,
    *,
    level: str,
    thread: str,
    timestamp: int,
    line_number: int,
    raw_line: str,
    server_id: str,
    server_name: str,
) -> ObservationRecord | None:
    common = {
        "event_id": f"console:{line_number}",
        "timestamp": timestamp,
        "server_id": server_id,
        "server_name": server_name,
        "context": {
            "source": "minecraft_console",
            "level": level,
            "thread": thread,
            "line": line_number,
        },
        "raw": {"line": raw_line},
    }

    if chat := CHAT_RE.match(message):
        return ObservationRecord(
            **common,
            kind="CHAT",
            player_name=chat.group("player"),
            content=chat.group("message"),
            tags=["console_chat"],
        )
    if command := COMMAND_RE.match(message):
        return ObservationRecord(
            **common,
            kind="ADMIN_COMMAND",
            player_name=command.group("player"),
            content=f"{command.group('player')} 执行命令 {command.group('command')}",
            tags=["issue:daily:admin_command", "admin_command"],
        )
    if join := JOIN_RE.match(message):
        return ObservationRecord(
            **common,
            kind="PLAYER_JOIN",
            player_name=join.group("player"),
            content=message,
            tags=["player_join"],
        )
    if quit_match := QUIT_RE.match(message):
        return ObservationRecord(
            **common,
            kind="PLAYER_QUIT",
            player_name=quit_match.group("player"),
            content=message,
            tags=["player_quit"],
        )
    if lost := LOST_RE.match(message):
        return ObservationRecord(
            **common,
            kind="PLAYER_QUIT",
            player_name=lost.group("player"),
            content=f"{lost.group('player')} 掉线：{lost.group('reason')}",
            tags=["player_quit", "lost_connection"],
        )
    if done := DONE_RE.match(message):
        seconds = float(done.group("seconds"))
        if seconds >= 120.0:
            return ObservationRecord(
                **common,
                kind="SERVER_STARTUP",
                content=f"服务器启动耗时 {seconds:.3f}s，超过 120s 阈值",
                tags=["issue:bug:slow_startup", "startup_slow"],
            )
        return ObservationRecord(
            **common,
            kind="SERVER_STARTUP",
            content=f"服务器启动耗时 {seconds:.3f}s",
            tags=["server_startup"],
        )

    if level in {"WARN", "ERROR"}:
        return _console_issue_record(
            message,
            level=level,
            common=common,
        )
    return None


def _console_issue_record(
    message: str,
    *,
    level: str,
    common: dict,
) -> ObservationRecord | None:
    if _is_stack_noise(message):
        return None

    plugin, body = _split_plugin_message(message)
    issue_tag = _console_issue_tag(message, plugin, level)
    tags = [f"console:{level.lower()}"]
    if plugin:
        tags.append(f"plugin:{plugin}")
    if issue_tag:
        tags.append(f"issue:bug:{issue_tag}")
    elif level == "WARN":
        return None

    content = body if plugin and body else message
    if plugin and body:
        content = f"[{plugin}] {body}"
    return ObservationRecord(
        **common,
        kind="PLUGIN_ERROR",
        content=content,
        tags=tags,
    )


def _console_issue_tag(message: str, plugin: str, level: str) -> str:
    text = f"{plugin} {message}".lower()
    if "administrative or root user" in text or "offline/insecure mode" in text:
        return "server_security_warning"
    if "no attempt to authenticate usernames" in text:
        return "server_security_warning"
    if "unknown system variable 'wsrep_on'" in text or "flyway" in text and "mariadb" in text:
        return "database_warning"
    if "mythicmobs" in text and "configuration error" in text:
        return "mythicmobs_config_error"
    if "failed to convert json to nbt" in text:
        return "data_converter_error"
    if "cannot load plugins" in text or "failed to move old config" in text:
        return "plugin_config_error"
    if "cannot initialize" in text or "empty api key" in text:
        return "plugin_integration_warning"
    return "console_error" if level == "ERROR" else ""


def _split_plugin_message(message: str) -> tuple[str, str]:
    match = PLUGIN_RE.match(message)
    if not match:
        return "", message
    plugin = match.group("plugin").strip()
    body = match.group("body").strip()
    return plugin, body


def _is_stack_noise(message: str) -> bool:
    text = message.strip()
    return (
        text.startswith("at ")
        or text.startswith("... ")
        or text.startswith("Caused by:")
        or text.startswith("Suppressed:")
        or text in {"****************************"}
        or set(text) <= {"~"}
    )


def _timestamp_ms(
    base_date: _dt.date,
    day_offset: int,
    hour: int,
    minute: int,
    second: int,
    line_number: int,
) -> int:
    value = _dt.datetime.combine(
        base_date + _dt.timedelta(days=day_offset),
        _dt.time(hour, minute, second),
    )
    return int(value.timestamp() * 1000) + min(line_number, 999)
