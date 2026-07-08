from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

def _install_astrbot_image_stub():
    class Image:
        @classmethod
        def fromBytes(cls, value):
            return value

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    components = types.ModuleType("astrbot.api.message_components")
    components.Image = Image
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)
    sys.modules.setdefault("astrbot.api.message_components", components)


_install_astrbot_image_stub()

try:
    from astrbot_plugin_minecraft_adapter.handlers.server_query_commands import (
        ServerQueryCommandHandler,
    )
except ModuleNotFoundError:
    plugin_root = Path(__file__).resolve().parents[1]
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    from handlers.server_query_commands import ServerQueryCommandHandler


class ServerQueryCommandHandlerTests(unittest.TestCase):
    def test_collect_list_cards_splits_proxy_backends(self):
        handler = _handler()
        server = _server(
            players=[
                SimpleNamespace(name="Alice", server="survival"),
                SimpleNamespace(name="Bob", server="skyblock"),
                SimpleNamespace(name="Carol", server=""),
            ],
            status=_proxy_status(),
        )

        cards, error = asyncio.run(handler.collect_list_cards(server))

        self.assertEqual(error, "")
        self.assertEqual(
            [(sid, len(players), total, name) for sid, players, total, name in cards],
            [
                ("survival", 1, 2, "survival"),
                ("skyblock", 1, 1, "skyblock"),
                ("未标记子服", 1, 1, "未标记子服"),
            ],
        )

    def test_resolve_player_card_server_name_uses_player_list_fallback(self):
        handler = _handler()
        detail = SimpleNamespace(name="Alice", uuid="uuid-Alice", server="")
        server = _server(
            players=[
                SimpleNamespace(name="Alice", uuid="uuid-Alice", server="survival")
            ],
            status=_proxy_status(),
        )

        server_name = asyncio.run(
            handler.resolve_player_card_server_name(server, detail)
        )

        self.assertEqual(server_name, "survival")


def _handler():
    return ServerQueryCommandHandler(
        server_manager=SimpleNamespace(get_all_servers=lambda: {}),
        renderer=SimpleNamespace(),
        get_server_config=lambda sid: SimpleNamespace(
            target_sessions=["group:test"],
            text2image=False,
        ),
    )


def _server(players, status):
    rest_client = SimpleNamespace(
        get_players=lambda: _async_result((players, len(players), "")),
        get_server_status=lambda: _async_result((status, "")),
        get_server_info=lambda: _async_result((None, "")),
    )
    return SimpleNamespace(
        server_id="proxy",
        server_info=SimpleNamespace(name="Proxy"),
        connected=True,
        rest_client=rest_client,
    )


def _proxy_status():
    return SimpleNamespace(
        is_proxy=True,
        backends=[
            SimpleNamespace(
                name="survival",
                platform="Paper",
                version="1.20.4",
                online_players=2,
                max_players=20,
                uptime_formatted="1h",
                tps_1m=20.0,
                tps_5m=20.0,
                tps_15m=20.0,
                memory_used=100,
                memory_max=1024,
                memory_usage_percent=10,
            ),
            SimpleNamespace(
                name="skyblock",
                platform="Paper",
                version="1.20.4",
                online_players=1,
                max_players=20,
                uptime_formatted="1h",
                tps_1m=19.0,
                tps_5m=19.0,
                tps_15m=19.0,
                memory_used=100,
                memory_max=1024,
                memory_usage_percent=10,
            ),
        ],
    )


async def _async_result(value):
    return value


if __name__ == "__main__":
    unittest.main()
