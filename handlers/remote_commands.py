"""Remote Minecraft command execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


@dataclass
class CmdTarget:
    """A selectable command target (proxy itself or a backend server)."""

    label: str
    server: object
    target_server: str | None = None


class RemoteCommandHandler:
    def __init__(self, get_server_config):
        self.get_server_config = get_server_config

    async def build_all_cmd_targets(self, servers: list) -> list[CmdTarget]:
        targets: list[CmdTarget] = []
        for server in servers:
            targets.extend(await self.build_server_targets(server))
        return targets

    async def build_server_targets(self, server) -> list[CmdTarget]:
        if await self.server_is_proxy(server):
            return await self.build_proxy_targets(server)

        name = (
            server.server_info.name
            if server.server_info and server.server_info.name
            else server.server_id
        )
        return [CmdTarget(label=name, server=server, target_server=None)]

    async def build_proxy_targets(self, server) -> list[CmdTarget]:
        info, _ = await server.rest_client.get_server_info()
        proxy_label = info.name if info and info.name else server.server_id
        targets = [
            CmdTarget(
                label=f"{proxy_label} (代理端)",
                server=server,
                target_server=None,
            )
        ]
        if info and info.backends:
            for backend in info.backends:
                if backend.name:
                    targets.append(
                        CmdTarget(
                            label=backend.name,
                            server=server,
                            target_server=backend.name,
                        )
                    )
        return targets

    async def server_is_proxy(self, server) -> bool:
        if server.server_info and server.server_info.is_proxy:
            return True
        info, _ = await server.rest_client.get_server_info()
        return info is not None and info.is_proxy

    def allowed_targets(
        self,
        command: str,
        targets: list[CmdTarget],
    ) -> tuple[list[CmdTarget], str]:
        allowed: list[CmdTarget] = []
        first_deny_message = ""
        for target in targets:
            ok, deny_message = self.is_cmd_allowed_on_server(command, target.server)
            if ok:
                allowed.append(target)
            elif not first_deny_message:
                first_deny_message = deny_message
        return allowed, first_deny_message

    def is_cmd_allowed_on_server(self, command: str, server) -> tuple[bool, str]:
        config = self.get_server_config(server.server_id)
        if not config or not config.cmd_enabled:
            return False, "❌ 远程指令功能未启用"

        if not self.check_command_allowed(command, config):
            return False, "❌ 此指令不在允许列表中"

        return True, ""

    async def do_cmd(
        self,
        event: "AstrMessageEvent",
        server,
        command: str,
        target_server: str | None = None,
    ):
        success, output, _ = await server.rest_client.execute_command(
            command,
            target_server=target_server,
        )

        target_label = f" [{target_server}]" if target_server else ""
        if success:
            yield event.plain_result(f"✅{target_label} 指令执行成功\n{output}")
        else:
            yield event.plain_result(f"❌{target_label} 指令执行失败: {output}")

    def format_target_choices(self, targets: list[CmdTarget]) -> str:
        lines = []
        for idx, target in enumerate(targets, start=1):
            server_id = target.server.server_id if target.server else ""
            if target.label and target.label != server_id:
                lines.append(f"{idx}. {target.label} [{server_id}]")
            else:
                lines.append(f"{idx}. {target.label}")
        return "\n".join(lines)

    @staticmethod
    def check_command_allowed(command: str, config) -> bool:
        parts = command.split()
        if not parts:
            return False
        command_name = parts[0].lower()

        command_list = [item.lower() for item in config.cmd_list]
        list_mode = (config.cmd_white_black_list or "white").lower()

        if list_mode == "none":
            return True

        if list_mode == "white":
            return command_name in command_list

        if list_mode == "black":
            return command_name not in command_list

        return command_name in command_list
