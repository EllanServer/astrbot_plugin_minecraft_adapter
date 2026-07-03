from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from astrbot_plugin_minecraft_adapter.handlers.remote_commands import (
        RemoteCommandHandler,
    )
except ModuleNotFoundError:
    plugin_root = Path(__file__).resolve().parents[1]
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    from handlers.remote_commands import RemoteCommandHandler


class RemoteCommandHandlerTests(unittest.TestCase):
    def test_check_command_allowed_modes(self):
        self.assertTrue(
            RemoteCommandHandler.check_command_allowed(
                "say hello",
                _config(mode="white", commands=["say"]),
            )
        )
        self.assertFalse(
            RemoteCommandHandler.check_command_allowed(
                "op Alice",
                _config(mode="white", commands=["say"]),
            )
        )
        self.assertFalse(
            RemoteCommandHandler.check_command_allowed(
                "stop",
                _config(mode="black", commands=["stop"]),
            )
        )
        self.assertTrue(
            RemoteCommandHandler.check_command_allowed(
                "stop",
                _config(mode="none", commands=[]),
            )
        )

    def test_build_proxy_targets_includes_proxy_and_backends(self):
        server = _server(proxy_info=_proxy_info())
        handler = RemoteCommandHandler(lambda sid: _config())

        targets = asyncio.run(handler.build_server_targets(server))

        self.assertEqual(
            [(target.label, target.target_server) for target in targets],
            [
                ("Velocity (代理端)", None),
                ("survival", "survival"),
                ("skyblock", "skyblock"),
            ],
        )

    def test_do_cmd_formats_target_output(self):
        server = _server()
        event = _FakeEvent()
        handler = RemoteCommandHandler(lambda sid: _config())

        result = asyncio.run(_collect(handler.do_cmd(event, server, "say hi", "survival")))

        self.assertEqual(result, ["✅ [survival] 指令执行成功\nok"])


def _config(mode="white", commands=None):
    return SimpleNamespace(
        cmd_enabled=True,
        cmd_white_black_list=mode,
        cmd_list=commands or ["say"],
    )


def _server(proxy_info=None):
    rest_client = SimpleNamespace(
        get_server_info=lambda: _async_result((proxy_info, "")),
        execute_command=lambda command, target_server=None: _async_result(
            (True, "ok", "")
        ),
    )
    return SimpleNamespace(
        server_id="proxy",
        server_info=SimpleNamespace(is_proxy=proxy_info is not None, name="Proxy"),
        rest_client=rest_client,
    )


def _proxy_info():
    return SimpleNamespace(
        name="Velocity",
        is_proxy=True,
        backends=[
            SimpleNamespace(name="survival"),
            SimpleNamespace(name="skyblock"),
        ],
    )


async def _async_result(value):
    return value


async def _collect(generator):
    return [item async for item in generator]


class _FakeEvent:
    def plain_result(self, text):
        return text


if __name__ == "__main__":
    unittest.main()
