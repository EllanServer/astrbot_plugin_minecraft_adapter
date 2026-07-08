import unittest

from astrbot_plugin_minecraft_adapter.core.models import ServerInfo, ServerStatus


REST_V2_INFO = {
    "protocolVersion": 2,
    "apiVersion": "v1",
    "servers": [
        {
            "id": "Leaf",
            "name": "Leaf",
            "displayName": "Leaf",
            "platform": "Paper",
            "version": "1.21.11-82-8c69a89 (MC: 1.21.11)",
            "motd": "A Minecraft Server",
            "onlinePlayers": 0,
            "maxPlayers": 20,
            "port": 25565,
            "scope": "local",
        }
    ],
    "aggregate": {
        "totalOnlinePlayers": 0,
        "totalMaxPlayers": 20,
        "backendCount": 0,
    },
}


REST_V2_STATUS = {
    "protocolVersion": 2,
    "apiVersion": "v1",
    "servers": [
        {
            "id": "Leaf",
            "name": "Leaf",
            "displayName": "Leaf",
            "online": True,
            "onlinePlayers": 0,
            "maxPlayers": 20,
            "uptime": 120294,
            "uptimeFormatted": "2分钟",
            "tps": {"1m": 19.96, "5m": 20.0, "15m": 19.95},
            "mspt": 0.46,
            "memory": {"used": 1636, "total": 4096, "max": 4096},
            "scope": "local",
        }
    ],
    "aggregate": {
        "totalOnlinePlayers": 0,
        "totalMaxPlayers": 20,
        "backendCount": 0,
    },
}


class RestV2ModelTests(unittest.TestCase):
    def test_server_info_uses_discovered_server_entry(self):
        info = ServerInfo.from_dict(REST_V2_INFO)

        self.assertEqual(info.server_id, "Leaf")
        self.assertEqual(info.name, "Leaf")
        self.assertEqual(info.platform, "Paper")
        self.assertEqual(info.minecraft_version, "1.21.11-82-8c69a89 (MC: 1.21.11)")
        self.assertEqual(info.aggregate_max, 20)
        self.assertEqual(len(info.discovered_servers), 1)
        self.assertFalse(info.is_proxy)

    def test_server_status_uses_discovered_server_entry(self):
        status = ServerStatus.from_dict(REST_V2_STATUS)

        self.assertTrue(status.online)
        self.assertEqual(status.online_players, 0)
        self.assertEqual(status.max_players, 20)
        self.assertEqual(status.uptime_formatted, "2分钟")
        self.assertAlmostEqual(status.tps_1m, 19.96)
        self.assertEqual(status.memory_max, 4096)
        self.assertEqual(len(status.discovered_servers), 1)
        self.assertFalse(status.is_proxy)


if __name__ == "__main__":
    unittest.main()
