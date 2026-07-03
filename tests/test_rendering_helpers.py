from __future__ import annotations

import asyncio
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from astrbot_plugin_minecraft_adapter.services.rendering import (
        flatten_player_cards,
        format_multi_player_list_text,
        format_player_detail_text,
        get_effective_server_name,
        mode_cn,
        safe_percent,
    )
except ModuleNotFoundError:
    _plugin_root = Path(__file__).resolve().parents[1]
    import sys

    if str(_plugin_root) not in sys.path:
        sys.path.insert(0, str(_plugin_root))
    from services.rendering import (
        flatten_player_cards,
        format_multi_player_list_text,
        format_player_detail_text,
        get_effective_server_name,
        mode_cn,
        safe_percent,
    )


class RenderingHelperTests(unittest.TestCase):
    def test_safe_percent_clamps_and_handles_bad_values(self):
        self.assertEqual(safe_percent(101.2), 100)
        self.assertEqual(safe_percent(-5), 0)
        self.assertEqual(safe_percent("bad"), 0)

    def test_mode_cn_maps_known_modes(self):
        self.assertEqual(mode_cn("SURVIVAL"), "生存")
        self.assertEqual(mode_cn("UNKNOWN"), "UNKNOWN")
        self.assertEqual(mode_cn(""), "未知")

    def test_effective_server_name_avoids_proxy_layer(self):
        player = SimpleNamespace(server="survival")

        self.assertEqual(get_effective_server_name(player, "velocity"), "survival")
        self.assertEqual(get_effective_server_name(SimpleNamespace(server=""), "proxy"), "")

    def test_flatten_player_cards_groups_proxy_players_by_backend(self):
        players = [
            SimpleNamespace(name="Alice", server="survival"),
            SimpleNamespace(name="Bob", server="skyblock"),
            SimpleNamespace(name="Carol", server="survival"),
        ]

        flattened = flatten_player_cards([("proxy", players, 3, "Velocity")])

        self.assertEqual(
            [(server_id, len(group)) for server_id, group, _, _ in flattened],
            [("survival", 2), ("skyblock", 1)],
        )

    def test_multi_player_list_text_uses_backend_names(self):
        players = [
            SimpleNamespace(name="Alice", server="survival", game_mode="SURVIVAL", world="world", ping=42),
            SimpleNamespace(name="Bob", server="skyblock", game_mode="CREATIVE", world="", ping=90),
        ]

        text = format_multi_player_list_text([("proxy", players, 2, "Velocity")])

        self.assertIn("服务器: survival (1人)", text)
        self.assertIn("Alice | 生存", text)
        self.assertIn("服务器: skyblock (1人)", text)

    def test_player_detail_text_avoids_proxy_server_name(self):
        player = SimpleNamespace(
            name="Alice",
            uuid="uuid-Alice",
            server="survival",
            world="world",
            game_mode="SURVIVAL",
            ping=42,
            health=20.0,
            max_health=20.0,
            food_level=20,
            level=12,
            exp=0.5,
            location=None,
            online_time_formatted="1h",
            is_op=True,
        )

        text = format_player_detail_text(player, server_tag="velocity")

        self.assertIn("权限: 管理员", text)
        self.assertIn("服务器: survival", text)

    def test_status_color_thresholds(self):
        image_helpers = _load_image_helpers_or_skip(self)

        self.assertEqual(image_helpers.status_color(19, "tps"), "#059669")
        self.assertEqual(image_helpers.status_color(150, "ping"), "#d97706")
        self.assertEqual(image_helpers.status_color(95, "memory"), "#dc2626")

    def test_info_renderer_text_mode_uses_facade(self):
        renderer_class = _load_info_renderer_or_skip(self)
        renderer = renderer_class(text2image_enabled=False)
        players = [
            SimpleNamespace(
                name="Alice",
                uuid="uuid-Alice",
                server="survival",
                game_mode="SURVIVAL",
                world="world",
                ping=42,
            )
        ]

        result = asyncio.run(
            renderer.render_multi_player_list(
                [("proxy", players, 1, "Velocity")],
                as_image=True,
            )
        )

        self.assertFalse(result.is_image)
        self.assertIn("服务器: survival", result.text)
        self.assertIn("Alice | 生存", result.text)

    def test_merge_images_vertical_size(self):
        image_helpers = _load_image_helpers_or_skip(self)
        pil_image = _load_pil_image_or_skip(self)

        first = pil_image.new("RGB", (10, 8), "#ffffff")
        second = pil_image.new("RGB", (6, 4), "#ffffff")

        out = image_helpers.merge_images_vertical([first, second], gap=2, pad=3)
        merged = pil_image.open(out)

        self.assertEqual(merged.size, (16, 20))


class AvatarProviderTests(unittest.TestCase):
    def test_avatar_provider_falls_back_and_deduplicates_batch_requests(self):
        provider_class = _load_avatar_provider_or_skip(self)
        asyncio.run(self._run_avatar_provider_case(provider_class))

    async def _run_avatar_provider_case(self, provider_class=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_cls = provider_class or _load_avatar_provider_or_skip(self)
            provider = _no_network_avatar_provider(provider_cls, Path(tmpdir))
            requests = [
                ("Alice", "uuid-Alice", 16),
                ("Alice", "uuid-Alice", 16),
                ("Bob", "uuid-Bob", 16),
            ]

            avatars = await provider.get_avatars(requests)

            self.assertEqual(provider.fetch_count, 2)
            self.assertEqual(len(avatars), 2)
            self.assertEqual(avatars[("Alice", "uuid-Alice", 16)].size, (16, 16))
            self.assertTrue((Path(tmpdir) / "alice_16.png").exists())


def _load_avatar_provider_or_skip(testcase: unittest.TestCase):
    try:
        from astrbot_plugin_minecraft_adapter.services.rendering.avatar import (
            AvatarProvider,
        )

        return AvatarProvider
    except ModuleNotFoundError:
        import sys

        _plugin_root = Path(__file__).resolve().parents[1]
        if str(_plugin_root) not in sys.path:
            sys.path.insert(0, str(_plugin_root))
        _install_astrbot_logger_stub()
        try:
            from services.rendering.avatar import AvatarProvider

            return AvatarProvider
        except ModuleNotFoundError as exc:
            testcase.skipTest(f"avatar dependencies unavailable: {exc}")


def _load_image_helpers_or_skip(testcase: unittest.TestCase):
    try:
        from astrbot_plugin_minecraft_adapter.services.rendering import image

        return image
    except ModuleNotFoundError:
        import sys

        _plugin_root = Path(__file__).resolve().parents[1]
        if str(_plugin_root) not in sys.path:
            sys.path.insert(0, str(_plugin_root))
        try:
            from services.rendering import image

            return image
        except ModuleNotFoundError as exc:
            testcase.skipTest(f"image helper dependencies unavailable: {exc}")


def _load_info_renderer_or_skip(testcase: unittest.TestCase):
    _install_astrbot_logger_stub()
    try:
        from astrbot_plugin_minecraft_adapter.services.renderer import InfoRenderer

        return InfoRenderer
    except ModuleNotFoundError:
        import sys

        _plugin_root = Path(__file__).resolve().parents[1]
        if str(_plugin_root) not in sys.path:
            sys.path.insert(0, str(_plugin_root))
        try:
            from services.renderer import InfoRenderer

            return InfoRenderer
        except ModuleNotFoundError as exc:
            testcase.skipTest(f"renderer dependencies unavailable: {exc}")


def _load_pil_image_or_skip(testcase: unittest.TestCase):
    try:
        from PIL import Image

        return Image
    except ModuleNotFoundError as exc:
        testcase.skipTest(f"Pillow unavailable: {exc}")


def _install_astrbot_logger_stub():
    import sys

    if "astrbot.api" in sys.modules:
        return

    class _Logger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _Logger()
    sys.modules.update({"astrbot": astrbot, "astrbot.api": api})


def _no_network_avatar_provider(base_class, avatar_dir: Path):
    class _NoNetworkAvatarProvider(base_class):
        def __init__(self, path: Path):
            super().__init__(path)
            self.fetch_count = 0

        async def fetch_avatar_face(self, player_name: str, player_uuid: str):
            self.fetch_count += 1
            return None

    return _NoNetworkAvatarProvider(avatar_dir)


if __name__ == "__main__":
    unittest.main()
