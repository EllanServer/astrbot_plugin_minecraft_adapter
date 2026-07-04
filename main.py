"""AstrBot 的 Minecraft 适配器插件

本插件实现了 Minecraft 服务器与 AstrBot 之间的通信，
提供 AI 聊天、消息转发和服务器管理功能。
"""

import asyncio
from contextlib import suppress
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.models import MCMessage, MessageType, ServerConfig, ServerInfo
from .core.server_manager import ServerManager
from .handlers.commands import CommandHandler
from .platform.adapter import MCPlatformAdapter
from .services.binding import BindingService
from .services.message_bridge import MessageBridge
from .services.mine_sentinel import MineSentinelService
from .services.renderer import InfoRenderer


AUTO_SERVER_ID_PLACEHOLDERS = {"", "auto", "my_server"}


class MinecraftAdapterPlugin(Star):
    """Minecraft 适配器主插件类"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

        # 获取插件数据路径
        plugin_data_path = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_minecraft_adapter"
        )
        plugin_data_path.mkdir(parents=True, exist_ok=True)
        self._plugin_data_path = plugin_data_path

        # 初始化服务
        self.server_manager = ServerManager()
        self.binding_service = BindingService(plugin_data_path)
        self.message_bridge = MessageBridge(context, self.server_manager)
        self.mine_sentinel_service = MineSentinelService(
            context=context,
            config_data=self.config.get("mine_sentinel", {}),
            get_server_config=lambda sid: self._server_configs.get(sid),
            storage_dir=plugin_data_path / "mine_sentinel",
        )

        # 服务器配置缓存
        self._server_configs: dict[str, ServerConfig] = {}
        self._server_config_entries: dict[str, dict] = {}

        # 平台适配器
        self._adapters: dict[str, MCPlatformAdapter] = {}
        self._adapter_tasks: dict[str, asyncio.Task] = {}

        # 命令处理器
        self.command_handler: CommandHandler | None = None

        # 设置消息处理器
        self.server_manager.set_message_handler(self._on_server_message)
        self.server_manager.set_connect_handler(self._on_server_connect)
        self.server_manager.set_disconnect_handler(self._on_server_disconnect)

        # 后台任务
        self._init_task: asyncio.Task | None = None

        # 加载配置并启动服务器
        self._init_task = self._schedule_task(self._initialize(), "initialize")

    def _schedule_task(self, coro, task_name: str) -> asyncio.Task | None:
        """创建后台任务并统一处理异常，避免未捕获异常导致静默失败。"""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError as exc:
            coro.close()
            logger.error(f"[MC Adapter] 无法启动后台任务 {task_name}: {exc}")
            return None

        task.add_done_callback(lambda t: self._on_task_done(task_name, t))
        return task

    def _on_task_done(self, task_name: str, task: asyncio.Task):
        """后台任务完成时的统一回调。"""
        try:
            exc = task.exception()
            if exc:
                logger.error(f"[MC Adapter] 后台任务 {task_name} 异常退出: {exc}")
        except asyncio.CancelledError:
            pass

    def _save_config(self):
        save_config = getattr(self.config, "save_config", None)
        if not callable(save_config):
            return
        try:
            save_config()
        except Exception as exc:
            logger.warning(f"[MC Adapter] 保存自动发现配置失败: {exc}")

    @staticmethod
    def _is_auto_server_id(value: str | None) -> bool:
        return str(value or "").strip().lower() in AUTO_SERVER_ID_PLACEHOLDERS

    @staticmethod
    def _temporary_server_id(config: ServerConfig, index: int) -> str:
        host = "".join(ch if ch.isalnum() else "_" for ch in config.host).strip("_")
        return f"auto_{host or 'server'}_{config.port}_{index + 1}"

    @staticmethod
    def _normalize_discovered_id(value: str) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _primary_discovered_entry(entries: list) -> object | None:
        for entry in entries:
            if (getattr(entry, "scope", "") or "").lower() != "backend":
                return entry
        return entries[0] if entries else None

    def _configured_server_id(self, runtime_id: str) -> str:
        entry = self._server_config_entries.get(runtime_id) or {}
        server = entry.get("server", {}) if isinstance(entry, dict) else {}
        return str(server.get("server_id") or runtime_id)

    async def _initialize(self):
        """初始化插件"""
        if not self.config.get("enabled", True):
            logger.info("[MC Adapter] 插件已禁用")
            return

        # 解析服务器配置
        mc_servers = self.config.get("mc_servers", [])
        if not mc_servers:
            logger.warning("[MC Adapter] 未配置任何服务器")
            return

        config_changed = False
        for index, server_data in enumerate(mc_servers):
            if not isinstance(server_data, dict):
                continue

            if not server_data.get("__template_key") and not server_data.get(
                "template"
            ):
                server_data["__template_key"] = "server"
                config_changed = True
            if "auto_server_id" not in server_data:
                server_data["auto_server_id"] = True
                config_changed = True

            if not server_data.get("enabled", True):
                continue

            config = ServerConfig.from_dict(server_data)
            if self._is_auto_server_id(config.server_id):
                config.server_id = self._temporary_server_id(config, index)

            if config.server_id in self._server_configs:
                logger.warning(f"[MC Adapter] 跳过重复服务器 ID: {config.server_id}")
                continue
            if not config.server_id:
                logger.warning("[MC Adapter] 跳过 ID 为空的服务器")
                continue

            self._server_configs[config.server_id] = config
            self._server_config_entries[config.server_id] = server_data

            # 将服务器添加到管理器
            self.server_manager.add_server(config)

            # 注册到消息桥接
            self.message_bridge.register_server(config)

            logger.info(f"[MC Adapter] 已配置服务器: {config.server_id}")

        # 初始化渲染器
        # 检查是否有任何服务器启用了 text2image
        if config_changed:
            self._save_config()

        any_text2image = any(c.text2image for c in self._server_configs.values())
        renderer = InfoRenderer(
            text2image_enabled=any_text2image,
            cache_dir=self._plugin_data_path / "renderer_cache",
        )

        # 初始化命令处理器
        self.command_handler = CommandHandler(
            server_manager=self.server_manager,
            binding_service=self.binding_service,
            renderer=renderer,
            get_server_config=lambda sid: self._server_configs.get(sid),
            mine_sentinel_service=self.mine_sentinel_service,
            session_matcher=self.message_bridge.session_matches,
        )

        # 为每个服务器注册自定义命令
        for server_id, config in self._server_configs.items():
            if config.custom_cmd_list:
                self.command_handler.register_custom_commands(
                    server_id, config.custom_cmd_list
                )

        # 启动所有服务器
        self.mine_sentinel_service.start()
        await self.server_manager.start_all()

        logger.info(
            f"[MC Adapter] 插件已初始化，配置了 {len(self._server_configs)} 个服务器"
        )

    async def _on_server_message(self, server_id: str, msg: MCMessage):
        """处理来自 MC 服务器的消息"""
        config = self._server_configs.get(server_id)
        if not config:
            return

        logger.debug(f"[MC-{server_id}] 收到消息类型: {msg.type}")

        if msg.type == MessageType.OBSERVATION_BATCH:
            await self.mine_sentinel_service.handle_batch(server_id, msg.payload or {})

        elif msg.type == MessageType.CHAT_REQUEST:
            payload = msg.payload or {}
            chat_mode = payload.get("chatMode", "GROUP")
            content = payload.get("content", "")
            source_server = (
                msg.source.server_name if msg.source and msg.source.server_name else ""
            )
            source_player = (
                msg.source.player_name if msg.source and msg.source.player_name else ""
            )
            source_label = source_server or server_id
            logger.info(
                f"[MC-{server_id}] 收到AI聊天消息: {source_label}"
                f"/{source_player} -> {content} [{chat_mode}]"
            )
            # AI 聊天请求 - 转发到平台适配器
            adapter = self._adapters.get(server_id)
            if adapter:
                await adapter.handle_chat_request(msg)

        elif msg.type in (
            MessageType.MESSAGE_FORWARD,
            MessageType.PLAYER_JOIN,
            MessageType.PLAYER_QUIT,
        ):
            # 转发到外部会话
            await self.message_bridge.handle_mc_message(server_id, msg)

    async def _discover_runtime_server_id(
        self,
        server_id: str,
        info: ServerInfo,
    ) -> str:
        entries = list(getattr(info, "discovered_servers", []) or [])
        server = self.server_manager.get_server(server_id)
        if server:
            rest_info, error = await server.rest_client.get_server_info()
            if rest_info:
                entries = list(
                    getattr(rest_info, "discovered_servers", []) or entries
                )
                if not entries:
                    return rest_info.server_id or rest_info.name or server_id
            elif error:
                logger.debug(f"[MC-{server_id}] 自动发现服务器 ID 失败: {error}")

        configured_id = self._configured_server_id(server_id)
        configured_norm = self._normalize_discovered_id(configured_id)
        for entry in entries:
            aliases = {
                self._normalize_discovered_id(getattr(entry, "server_id", "")),
                self._normalize_discovered_id(getattr(entry, "name", "")),
                self._normalize_discovered_id(getattr(entry, "display_name", "")),
            }
            if configured_norm and configured_norm in aliases:
                return getattr(entry, "server_id", "") or getattr(entry, "name", "")

        primary = self._primary_discovered_entry(entries)
        if primary:
            return getattr(primary, "server_id", "") or getattr(primary, "name", "")

        return info.server_id or info.name or server_id

    def _rename_runtime_server(self, old_id: str, new_id: str) -> str:
        new_id = str(new_id or "").strip()
        if not new_id or old_id == new_id:
            return old_id

        config = self._server_configs.get(old_id)
        if not config:
            return old_id
        if new_id in self._server_configs:
            logger.warning(f"[MC Adapter] 自动发现的服务器 ID 已存在，跳过: {new_id}")
            return old_id
        if not self.server_manager.rename_server(old_id, new_id):
            return old_id

        self._server_configs.pop(old_id, None)
        config.server_id = new_id
        self._server_configs[new_id] = config

        entry = self._server_config_entries.pop(old_id, None)
        if isinstance(entry, dict):
            server_data = entry.setdefault("server", {})
            if (
                isinstance(server_data, dict)
                and server_data.get("server_id") != new_id
            ):
                server_data["server_id"] = new_id
                entry.setdefault("__template_key", "server")
                self._save_config()
            self._server_config_entries[new_id] = entry

        self.message_bridge.rename_server(old_id, new_id, config)
        if self.command_handler:
            self.command_handler.rename_server(old_id, new_id)

        adapter = self._adapters.pop(old_id, None)
        if adapter:
            self._adapters[new_id] = adapter
        task = self._adapter_tasks.pop(old_id, None)
        if task:
            self._adapter_tasks[new_id] = task

        logger.info(f"[MC Adapter] 已根据扫描结果自动匹配服务器: {old_id} -> {new_id}")
        return new_id

    async def _sync_discovered_server_id(
        self,
        server_id: str,
        info: ServerInfo,
    ) -> str:
        config = self._server_configs.get(server_id)
        if not config:
            return server_id

        configured_id = self._configured_server_id(server_id)
        if not config.auto_server_id and not self._is_auto_server_id(configured_id):
            return server_id

        discovered_id = await self._discover_runtime_server_id(server_id, info)
        return self._rename_runtime_server(server_id, discovered_id)

    async def _refresh_display_server_info(
        self,
        server_id: str,
        info: ServerInfo,
    ) -> ServerInfo:
        if info.name and info.platform:
            return info

        server = self.server_manager.get_server(server_id)
        if not server:
            return info

        rest_info, error = await server.rest_client.get_server_info()
        if not rest_info:
            if error:
                logger.debug(f"[MC-{server_id}] 刷新服务器显示信息失败: {error}")
            return info

        server.ws_client._server_info = rest_info
        return rest_info

    async def _on_server_connect(self, server_id: str, info: ServerInfo):
        """处理服务器连接"""
        config = self._server_configs.get(server_id)
        if not config:
            return

        server_id = await self._sync_discovered_server_id(server_id, info)
        config = self._server_configs.get(server_id)
        if not config:
            return
        info = await self._refresh_display_server_info(server_id, info)

        logger.info(
            f"[MC-{server_id}] 已连接到 {info.name} "
            f"({info.platform} {info.minecraft_version})"
        )

        # 如果启用了 AI 聊天，创建平台适配器
        if config.enable_ai_chat:
            server = self.server_manager.get_server(server_id)
            if server:
                # 防止重复连接时遗留旧适配器/任务
                old_adapter = self._adapters.pop(server_id, None)
                if old_adapter:
                    await old_adapter.stop()

                old_task = self._adapter_tasks.pop(server_id, None)
                if old_task and not old_task.done():
                    old_task.cancel()

                event_queue = (
                    self.context.platform_manager.event_queue
                    if self.context and self.context.platform_manager
                    else asyncio.Queue()
                )
                adapter = MCPlatformAdapter(
                    server_config=config,
                    server_connection=server,
                    event_queue=event_queue,
                )
                self._adapters[server_id] = adapter

                # 启动适配器（非阻塞）
                task = self._schedule_task(adapter.run(), f"adapter:{server_id}")
                if task:
                    self._adapter_tasks[server_id] = task

                logger.info(f"[MC-{server_id}] 平台适配器已注册")

    async def _on_server_disconnect(self, server_id: str, reason: str):
        """处理服务器断开连接"""
        logger.warning(f"[MC-{server_id}] 已断开连接: {reason}")

        # 停止平台适配器
        adapter = self._adapters.pop(server_id, None)
        if adapter:
            await adapter.stop()

        task = self._adapter_tasks.pop(server_id, None)
        if task and not task.done():
            task.cancel()

    # 命令处理器

    @filter.command_group("mc")
    def mc_group(self):
        """我的世界服务器管理命令"""
        pass

    @mc_group.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        async for result in self._dispatch_command("handle_help", event):
            yield result

    @mc_group.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看服务器状态"""
        async for result in self._dispatch_command("handle_status", event):
            yield result

    @mc_group.command("list")
    async def cmd_list(self, event: AstrMessageEvent):
        """查看在线玩家列表"""
        async for result in self._dispatch_command("handle_list", event):
            yield result

    @mc_group.command("player")
    async def cmd_player(self, event: AstrMessageEvent, player_id: str):
        """查看玩家详细信息"""
        async for result in self._dispatch_command("handle_player", event, player_id):
            yield result

    @mc_group.command("cmd")
    async def cmd_execute(self, event: AstrMessageEvent, command=GreedyStr):
        """远程执行服务器指令"""
        async for result in self._dispatch_command("handle_cmd", event, str(command)):
            yield result

    @mc_group.command("monitor")
    async def cmd_monitor(self, event: AstrMessageEvent, args=GreedyStr):
        """MineSentinel 监控状态"""
        async for result in self._dispatch_command("handle_monitor", event, str(args)):
            yield result

    @mc_group.command("report")
    async def cmd_report(self, event: AstrMessageEvent, args=GreedyStr):
        """MineSentinel 报告"""
        async for result in self._dispatch_command("handle_report", event, str(args)):
            yield result

    @mc_group.command("bind")
    async def cmd_bind(self, event: AstrMessageEvent, player_id: str):
        """绑定游戏ID"""
        async for result in self._dispatch_command("handle_bind", event, player_id):
            yield result

    @mc_group.command("unbind")
    async def cmd_unbind(self, event: AstrMessageEvent):
        """解除绑定"""
        async for result in self._dispatch_command("handle_unbind", event):
            yield result

    async def _dispatch_command(self, method_name: str, event: AstrMessageEvent, *args):
        """统一分发到命令处理器，减少重复样板代码。"""
        if not self.command_handler:
            return

        handler = getattr(self.command_handler, method_name, None)
        if not handler:
            logger.warning(f"[MC Adapter] 未找到命令处理方法: {method_name}")
            return

        async for result in handler(event, *args):
            yield result

    # 消息转发监听器

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听需要转发到 MC 服务器的消息，并处理自定义指令和待选操作

        Note: mc 子命令（help/status/list 等）已由 @mc_group.command()
        装饰器处理，此处仅处理：
        1. 待选操作的数字输入（多服务器选择）
        2. 自定义指令匹配
        3. 消息转发到 MC 服务器
        """
        # Skip messages already handled by command group (wake prefix / @)
        if event.is_at_or_wake_command:
            return

        if not self.command_handler:
            # No command handler, only try message forwarding
            if await self.message_bridge.handle_external_message(event):
                event.stop_event()
            return

        text = event.get_message_str().strip()
        umo = event.unified_msg_origin

        # Handle pending server/backend selection (number input)
        if text and text.isdigit() and self.command_handler.has_pending_action(umo):
            async for result in self.command_handler.dispatch_number_selection(event):
                yield result
            event.stop_event()
            return

        # Check custom commands
        async for result in self.command_handler.handle_custom_command(event):
            yield result
        if event.get_extra("custom_cmd_matched"):
            event.stop_event()
            return

        # Forward message to MC server(s)
        if await self.message_bridge.handle_external_message(event):
            event.stop_event()

    async def terminate(self):
        """插件终止时的清理工作"""
        logger.info("[MC Adapter] 正在关闭...")

        # 停止初始化任务
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._init_task
        self._init_task = None

        # 停止所有适配器
        for adapter in self._adapters.values():
            await adapter.stop()
        self._adapters.clear()

        # 取消所有适配器任务
        for task in self._adapter_tasks.values():
            if not task.done():
                task.cancel()
        if self._adapter_tasks:
            await asyncio.gather(*self._adapter_tasks.values(), return_exceptions=True)
        self._adapter_tasks.clear()

        # 停止所有服务器连接
        await self.server_manager.stop_all()

        await self.mine_sentinel_service.stop()

        logger.info("[MC Adapter] 关闭完成")
