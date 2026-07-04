"""Minecraft 适配器插件的命令处理器"""

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .binding_commands import BindingCommandHandler
from .custom_commands import CustomCommandParser
from .mine_sentinel_commands import MineSentinelCommandHandler, parse_window_minutes
from .remote_commands import CmdTarget, RemoteCommandHandler
from .server_query_commands import ServerQueryCommandHandler
from .session_state import (
    SESSION_BINDING_ERROR,
    CommandSessionState,
    format_server_choices,
)

if TYPE_CHECKING:
    from ..core.server_manager import ServerManager
    from ..services.binding import BindingService
    from ..services.mine_sentinel import MineSentinelService
    from ..services.renderer import InfoRenderer


class CommandHandler:
    """所有 mc 命令的处理器"""

    def __init__(
        self,
        server_manager: "ServerManager",
        binding_service: "BindingService",
        renderer: "InfoRenderer",
        get_server_config,
        mine_sentinel_service: "MineSentinelService | None" = None,
        session_matcher=None,
    ):
        self.server_manager = server_manager
        self.binding_service = binding_service
        self.renderer = renderer
        self.get_server_config = get_server_config
        self.mine_sentinel_service = mine_sentinel_service
        self.mine_sentinel_commands = MineSentinelCommandHandler(mine_sentinel_service)
        self.remote_commands = RemoteCommandHandler(get_server_config)
        self.session_state = CommandSessionState(
            server_manager,
            get_server_config,
            session_matcher=session_matcher,
        )
        self.binding_commands = BindingCommandHandler(
            binding_service,
            get_server_config,
            self.session_state.resolve_server_or_pending,
        )
        self.server_query_commands = ServerQueryCommandHandler(
            server_manager,
            renderer,
            get_server_config,
            self.session_state,
        )
        self._custom_parsers: dict[str, CustomCommandParser] = {}

    def register_custom_commands(self, server_id: str, mappings: list[str]):
        """为服务器注册自定义命令"""
        self._custom_parsers[server_id] = CustomCommandParser(mappings)
        logger.info(
            f"[CommandHandler] 已为服务器 {server_id} 注册了 {len(mappings)} 个自定义命令"
        )

    def rename_server(self, old_id: str, new_id: str):
        """Move runtime command state to a discovered server id."""
        if old_id == new_id:
            return
        parser = self._custom_parsers.pop(old_id, None)
        if parser is not None:
            self._custom_parsers[new_id] = parser

    async def handle_custom_command(self, event: AstrMessageEvent):
        """Try to match and execute a custom command from the message text.

        Async generator: yields results if a custom command was matched.
        Sets event extra 'custom_cmd_matched' to True if matched.

        流程: 参数解析/匹配 → 收集所有匹配的服务器目标 → 目标选择 → 执行
        (跳过黑白名单——管理员配置的自定义指令是受信任的)
        """
        message_str = event.message_str.strip()
        if not message_str:
            return

        umo = event.unified_msg_origin

        # Collect all matching servers and their resolved commands
        all_targets: list[CmdTarget] = []
        matched_command: str | None = None
        first_missing_usage: str | None = None

        for server_id, parser in self._custom_parsers.items():
            config = self.get_server_config(server_id)
            if not config:
                continue
            if not self.session_state.config_matches_session(config, umo):
                continue
            if not config.cmd_enabled:
                continue

            # Check missing usage (show hint from first match)
            usage = parser.get_missing_usage(message_str)
            if usage and first_missing_usage is None:
                first_missing_usage = usage

            # Get sender's bound MC name
            sender_mc_name = None
            if config.bind_enable:
                platform = event.get_platform_name()
                user_id = event.get_sender_id()
                binding = self.binding_service.get_binding(platform, user_id)
                sender_mc_name = binding.mc_player_name if binding else None

            result = parser.match(message_str, sender_mc_name)
            if result:
                command, _ = result
                matched_command = command
                server = self.server_manager.get_server(server_id)
                if not server or not server.connected:
                    continue

                # Build targets for this server (reuse common method)
                targets = await self.remote_commands.build_server_targets(server)
                all_targets.extend(targets)

        # If no match found but missing usage detected, show hint
        if not all_targets and first_missing_usage:
            yield event.plain_result(f"❌ 参数不足，格式: {first_missing_usage}")
            event.set_extra("custom_cmd_matched", True)
            return

        if all_targets and matched_command:
            # Execute or prompt for selection across all matching servers
            async for r in self._execute_or_select_target(
                event, all_targets, matched_command, action="custom_cmd"
            ):
                yield r
            event.set_extra("custom_cmd_matched", True)
            return

    def has_pending_action(self, umo: str) -> bool:
        """Check if a session has a valid pending action."""
        return self.session_state.has_pending_action(umo)

    async def dispatch_number_selection(self, event: AstrMessageEvent):
        """Dispatch a number selection to the pending action."""
        umo = event.unified_msg_origin
        pending = self.session_state.pop_pending_action(umo)
        if not pending:
            return

        text = event.message_str.strip()
        if not text.isdigit():
            yield event.plain_result("❌ 请发送有效的数字编号")
            self.session_state.restore_pending_action(umo, pending)
            return

        idx = int(text)
        action = pending.action
        args = pending.args

        if pending.cmd_targets:
            # Unified cmd target selection (proxy + backends)
            if idx < 1 or idx > len(pending.cmd_targets):
                choices = self.remote_commands.format_target_choices(pending.cmd_targets)
                yield event.plain_result(f"❌ 编号无效，请从以下列表中选择:\n{choices}")
                self.session_state.restore_pending_action(umo, pending)
                return

            target = pending.cmd_targets[idx - 1]
            server = target.server
            target_server = target.target_server

            # Auth check only for user-initiated cmd
            if action == "cmd":
                allowed, deny_message = self.remote_commands.is_cmd_allowed_on_server(
                    args["command"], server
                )
                if not allowed:
                    yield event.plain_result(deny_message)
                    return
            async for result in self.remote_commands.do_cmd(
                event, server, args["command"], target_server=target_server
            ):
                yield result
        else:
            # Server selection (multi-server mode) for non-cmd actions
            if idx < 1 or idx > len(pending.servers):
                choices = format_server_choices(pending.servers)
                yield event.plain_result(f"❌ 编号无效，请从以下列表中选择:\n{choices}")
                self.session_state.restore_pending_action(umo, pending)
                return

            server = pending.servers[idx - 1]

            async for result in self._dispatch_server_action(
                event, action, server, args
            ):
                yield result

    async def handle_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """📖 Minecraft 适配器指令帮助

基础指令:
    /mc help - 显示此帮助信息
    /mc status - 查看服务器状态
    /mc list - 查看在线玩家列表
    /mc player <玩家ID> - 查看玩家详细信息

远程指令:
    /mc cmd <指令> - 远程执行服务器指令

MineSentinel:
    /mc monitor status - 查看旁路监控状态
    /mc report now [服务器ID] [8h] - 立即生成观察报告

绑定功能:
    /mc bind <游戏ID> - 绑定你的游戏ID
    /mc unbind - 解除绑定

多服务器:
    status/list/player 会自动输出所有关联服务器结果
    cmd 在多目标下仍需编号选择"""

        # 收集自定义指令列表
        custom_cmds = self._get_custom_command_triggers()
        if custom_cmds:
            help_text += "\n\n自定义指令:\n"
            for trigger in custom_cmds:
                help_text += f"  {trigger}\n"
            help_text = help_text.rstrip("\n")

        yield event.plain_result(help_text)

    async def handle_monitor(self, event: AstrMessageEvent, args: str = ""):
        """MineSentinel monitor commands."""
        async for result in self.mine_sentinel_commands.handle_monitor(event, args):
            yield result

    async def handle_report(self, event: AstrMessageEvent, args: str = ""):
        """MineSentinel report commands."""
        async for result in self.mine_sentinel_commands.handle_report(event, args):
            yield result

    async def handle_status(self, event: AstrMessageEvent):
        """显示服务器状态"""
        async for result in self.server_query_commands.handle_status(event):
            yield result

    async def _do_status(self, event: AstrMessageEvent, server):
        """Execute status query on a resolved server"""
        async for result in self.server_query_commands.do_status(event, server):
            yield result

    async def handle_list(self, event: AstrMessageEvent):
        """显示在线玩家列表"""
        async for result in self.server_query_commands.handle_list(event):
            yield result

    async def _do_list(self, event: AstrMessageEvent, server):
        """Execute player list query on a resolved server"""
        async for result in self.server_query_commands.do_list(event, server):
            yield result

    async def handle_player(self, event: AstrMessageEvent, player_id: str):
        """显示玩家详细信息"""
        async for result in self.server_query_commands.handle_player(event, player_id):
            yield result

    async def _do_player(self, event: AstrMessageEvent, server, player_id: str):
        """Execute player detail query on a resolved server"""
        async for result in self.server_query_commands.do_player(
            event,
            server,
            player_id,
        ):
            yield result

    async def handle_cmd(self, event: AstrMessageEvent, command: str):
        """执行远程命令

        流程: 构建目标列表(含proxy展开) → cmd_enabled/黑白名单检查 → 目标选择 → 执行
        """
        if not command:
            yield event.plain_result("❌ 请指定要执行的指令")
            return

        umo = event.unified_msg_origin
        servers = self._get_session_servers(umo)
        if not servers:
            yield event.plain_result(SESSION_BINDING_ERROR)
            return

        # Build unified target list across all servers
        all_targets = await self.remote_commands.build_all_cmd_targets(servers)
        if not all_targets:
            yield event.plain_result("❌ 没有可用的执行目标")
            return

        allowed_targets, first_deny_message = self.remote_commands.allowed_targets(
            command,
            all_targets,
        )

        if not allowed_targets:
            yield event.plain_result(first_deny_message or "❌ 没有可用的执行目标")
            return

        # Execute or prompt for selection
        async for result in self._execute_or_select_target(
            event, allowed_targets, command, action="cmd"
        ):
            yield result

    async def _dispatch_server_action(
        self, event: AstrMessageEvent, action: str, server, args: dict
    ):
        """Dispatch non-cmd pending actions to concrete executors."""
        if action == "status":
            async for result in self._do_status(event, server):
                yield result
            return

        if action == "list":
            async for result in self._do_list(event, server):
                yield result
            return

        if action == "player":
            async for result in self._do_player(
                event, server, args.get("player_id", "")
            ):
                yield result
            return

        if action == "bind":
            async for result in self.binding_commands.do_bind(
                event,
                server,
                args.get("player_id", ""),
            ):
                yield result

    async def _execute_or_select_target(
        self,
        event: AstrMessageEvent,
        targets: list[CmdTarget],
        command: str,
        action: str = "cmd",
    ):
        """Execute directly if single target, otherwise prompt user to select."""
        if not targets:
            yield event.plain_result("❌ 没有可用的执行目标")
            return

        if len(targets) == 1:
            t = targets[0]
            async for result in self.remote_commands.do_cmd(
                event, t.server, command, target_server=t.target_server
            ):
                yield result
            return

        # Multiple targets: prompt user to select
        choices = self.remote_commands.format_target_choices(targets)
        umo = event.unified_msg_origin
        self.session_state.set_cmd_target_selection(
            umo,
            action,
            targets,
            command,
        )
        yield event.plain_result(f"⚠️ 请选择执行目标:\n{choices}")

    async def handle_bind(self, event: AstrMessageEvent, player_id: str):
        """绑定用户到 MC 玩家"""
        async for result in self.binding_commands.handle_bind(event, player_id):
            yield result

    async def _do_bind(self, event: AstrMessageEvent, server, player_id: str):
        """Execute bind on a resolved server"""
        async for result in self.binding_commands.do_bind(event, server, player_id):
            yield result

    async def handle_unbind(self, event: AstrMessageEvent):
        """解绑用户与 MC 玩家的绑定"""
        async for result in self.binding_commands.handle_unbind(event):
            yield result

    def _get_custom_command_triggers(self) -> list[str]:
        """获取所有服务器的自定义命令触发词列表（去重）"""
        triggers = []
        seen: set[str] = set()
        for server_id in self._custom_parsers:
            config = self.get_server_config(server_id)
            if not config or not config.custom_cmd_list:
                continue
            for mapping_str in config.custom_cmd_list:
                if CustomCommandParser.SEPARATOR in mapping_str:
                    trigger_part = mapping_str.split(CustomCommandParser.SEPARATOR, 1)[
                        0
                    ].strip()
                    if trigger_part not in seen:
                        seen.add(trigger_part)
                        triggers.append(trigger_part)
        return triggers

    def _get_session_servers(self, umo: str) -> list:
        return self.session_state.get_session_connected_servers(umo)

    def _format_server_choices(self, servers: list) -> str:
        return format_server_choices(servers)

    def _resolve_server_or_pending(
        self,
        umo: str,
        action: str = "",
        args: dict | None = None,
    ) -> tuple[object | None, str]:
        """Resolve the target server for a command.

        If only one server is associated, return it directly.
        If multiple servers are associated, create a pending action and
        return the server choice prompt. Returns (None, prompt_msg) when pending.
        Returns (None, error_msg) on error.
        Returns (server, "") on success.
        """
        return self.session_state.resolve_server_or_pending(umo, action, args)

    @staticmethod
    def _parse_window_minutes(value: str) -> int | None:
        return parse_window_minutes(value)
