"""Session-scoped command selection state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


SESSION_BINDING_ERROR = "❌ 当前会话未关联任何服务器，请在插件配置中将此会话添加到服务器的目标会话列表"
PENDING_ACTION_TIMEOUT = 60


@dataclass
class PendingAction:
    """A pending action waiting for the user to select a number."""

    action: str
    args: dict[str, Any] = field(default_factory=dict)
    servers: list[Any] = field(default_factory=list)
    cmd_targets: list[Any] = field(default_factory=list)
    timestamp: float = 0.0


class CommandSessionState:
    """Keeps command selection state out of concrete command handlers."""

    def __init__(
        self,
        server_manager: Any,
        get_server_config: Callable[[str], Any | None],
        timeout_seconds: int = PENDING_ACTION_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ):
        self.server_manager = server_manager
        self.get_server_config = get_server_config
        self.timeout_seconds = max(1, timeout_seconds)
        self.clock = clock
        self._pending_actions: dict[str, PendingAction] = {}

    def has_pending_action(self, umo: str) -> bool:
        pending = self._pending_actions.get(umo)
        if not pending:
            return False
        if self._expired(pending):
            self._pending_actions.pop(umo, None)
            return False
        return True

    def pop_pending_action(self, umo: str) -> PendingAction | None:
        pending = self._pending_actions.pop(umo, None)
        if pending and self._expired(pending):
            return None
        return pending

    def restore_pending_action(self, umo: str, pending: PendingAction):
        pending.timestamp = pending.timestamp or self.clock()
        self._pending_actions[umo] = pending

    def set_server_selection(
        self,
        umo: str,
        action: str,
        servers: list[Any],
        args: dict[str, Any] | None = None,
    ):
        self._pending_actions[umo] = PendingAction(
            action=action,
            args=args or {},
            servers=servers,
            timestamp=self.clock(),
        )

    def set_cmd_target_selection(
        self,
        umo: str,
        action: str,
        cmd_targets: list[Any],
        command: str,
    ):
        self._pending_actions[umo] = PendingAction(
            action=action,
            args={"command": command},
            cmd_targets=cmd_targets,
            timestamp=self.clock(),
        )

    def get_session_connected_servers(self, umo: str) -> list[Any]:
        return [
            server
            for server in self._session_servers(umo, connected_only=True)
        ]

    def get_session_all_servers(self, umo: str) -> list[Any]:
        return [
            server
            for server in self._session_servers(umo, connected_only=False)
        ]

    def resolve_server_or_pending(
        self,
        umo: str,
        action: str = "",
        args: dict[str, Any] | None = None,
    ) -> tuple[object | None, str]:
        servers = self.get_session_connected_servers(umo)
        if not servers:
            return None, SESSION_BINDING_ERROR
        if len(servers) == 1:
            return servers[0], ""

        self.set_server_selection(umo, action, servers, args)
        return (
            None,
            f"⚠️ 当前会话关联多个服务器，请发送编号选择:\n"
            f"{format_server_choices(servers)}",
        )

    def _session_servers(self, umo: str, connected_only: bool) -> list[Any]:
        if not umo:
            return []
        getter_name = "get_connected_servers" if connected_only else "get_all_servers"
        getter = getattr(self.server_manager, getter_name)
        raw_servers = getter()
        servers = raw_servers.values() if isinstance(raw_servers, dict) else raw_servers
        return [
            server
            for server in servers
            if self._server_matches_session(server, umo)
        ]

    def _server_matches_session(self, server: Any, umo: str) -> bool:
        config = self.get_server_config(server.server_id)
        return bool(config and config.target_sessions and umo in config.target_sessions)

    def _expired(self, pending: PendingAction) -> bool:
        return self.clock() - pending.timestamp > self.timeout_seconds


def format_server_choices(servers: list[Any]) -> str:
    lines = []
    for idx, server in enumerate(servers, start=1):
        name = (
            server.server_info.name
            if getattr(server, "server_info", None)
            and getattr(server.server_info, "name", "")
            else ""
        )
        name_part = f" ({name})" if name else ""
        lines.append(f"{idx}. {server.server_id}{name_part}")
    return "\n".join(lines)
