from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

try:
    from astrbot_plugin_minecraft_adapter.handlers.custom_commands import (
        CustomCommandParser,
    )
    from astrbot_plugin_minecraft_adapter.handlers.mine_sentinel_commands import (
        MineSentinelCommandHandler,
        parse_report_args,
        parse_window_minutes,
    )
    from astrbot_plugin_minecraft_adapter.handlers.session_state import (
        CommandSessionState,
        format_server_choices,
    )
except ModuleNotFoundError:
    from handlers.custom_commands import CustomCommandParser
    from handlers.mine_sentinel_commands import (
        MineSentinelCommandHandler,
        parse_report_args,
        parse_window_minutes,
    )
    from handlers.session_state import CommandSessionState, format_server_choices


class MineSentinelCommandGroupTests(unittest.TestCase):
    def test_parse_window_minutes(self):
        self.assertEqual(parse_window_minutes("8h"), 480)
        self.assertEqual(parse_window_minutes("30m"), 30)
        self.assertEqual(parse_window_minutes("15min"), 15)
        self.assertEqual(parse_window_minutes("2小时"), 120)
        self.assertIsNone(parse_window_minutes("survival"))

    def test_parse_report_args(self):
        target = parse_report_args(["survival", "8h"])

        self.assertEqual(target.server_id, "survival")
        self.assertEqual(target.window_minutes, 480)

    def test_report_now_delegates_to_service(self):
        result = asyncio.run(self._collect_report("now survival 8h", _FakeService()))

        self.assertEqual(result, ["report text"])

    def test_report_now_without_service_is_clear(self):
        result = asyncio.run(self._collect_report("now", service=None))

        self.assertEqual(result, ["MineSentinel 未初始化"])

    async def _collect_report(self, args: str, service):
        event = _FakeEvent()
        handler = MineSentinelCommandHandler(service)
        return [
            item
            async for item in handler.handle_report(event, args)
        ]


class CustomCommandParserTests(unittest.TestCase):
    def test_match_replaces_named_params_and_sender(self):
        parser = CustomCommandParser(
            ["tp <&x&> <&y&> <&z&><<>>tp {sender} {x} {y} {z}"]
        )

        result = parser.match("tp 1 64 -2", sender_mc_name="Alice")

        self.assertIsNotNone(result)
        command, params = result
        self.assertEqual(command, "tp Alice 1 64 -2")
        self.assertEqual(params["x"], "1")
        self.assertEqual(params["sender"], "Alice")

    def test_missing_usage_returns_trigger(self):
        parser = CustomCommandParser(["head <&player&><<>>give {sender} head {player}"])

        self.assertEqual(parser.get_missing_usage("head"), "head <&player&>")


class CommandSessionStateTests(unittest.TestCase):
    def test_resolve_server_or_pending_prompts_for_multiple_servers(self):
        state = _session_state(
            [
                _server("survival", "Survival"),
                _server("skyblock", "SkyBlock"),
            ]
        )

        server, prompt = state.resolve_server_or_pending(
            "group:test",
            action="status",
        )

        self.assertIsNone(server)
        self.assertIn("请发送编号选择", prompt)
        self.assertIn("1. survival (Survival)", prompt)
        pending = state.pop_pending_action("group:test")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.action, "status")
        self.assertEqual([item.server_id for item in pending.servers], ["survival", "skyblock"])

    def test_pending_action_expires(self):
        now = [100.0]
        state = _session_state(
            [_server("survival")],
            clock=lambda: now[0],
            timeout=10,
        )
        state.set_server_selection("group:test", "status", [_server("survival")])

        now[0] = 111.0

        self.assertFalse(state.has_pending_action("group:test"))
        self.assertIsNone(state.pop_pending_action("group:test"))

    def test_format_server_choices_uses_server_name_when_available(self):
        self.assertEqual(
            format_server_choices([_server("survival", "Survival")]),
            "1. survival (Survival)",
        )


class _FakeEvent:
    unified_msg_origin = "group:test"

    def plain_result(self, text):
        return text


class _FakeService:
    async def report_now(self, current_session, server_id=None, window_minutes=None):
        assert current_session == "group:test"
        assert server_id == "survival"
        assert window_minutes == 480
        return "report text"


def _session_state(servers, clock=None, timeout=60):
    manager = SimpleNamespace(
        get_connected_servers=lambda: servers,
        get_all_servers=lambda: {server.server_id: server for server in servers},
    )
    return CommandSessionState(
        manager,
        get_server_config=lambda sid: SimpleNamespace(target_sessions=["group:test"]),
        timeout_seconds=timeout,
        clock=clock or (lambda: 100.0),
    )


def _server(server_id: str, name: str = ""):
    return SimpleNamespace(
        server_id=server_id,
        connected=True,
        server_info=SimpleNamespace(name=name),
    )


if __name__ == "__main__":
    unittest.main()
