from __future__ import annotations

import asyncio
import gzip
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
api.logger = getattr(api, "logger", _Logger())
sys.modules.update({"astrbot": astrbot, "astrbot.api": api})

if "mine_sentinel_rs" not in sys.modules:
    native_stub = types.ModuleType("mine_sentinel_rs")

    class _ObservationRecordCodec:
        def __init__(
            self,
            max_content_length,
            max_tags_per_record,
            max_raw_fields,
            include_raw,
            dedupe_window_seconds,
        ):
            self.dedupe_window_seconds = max(1, int(dedupe_window_seconds))

        def dedupe_key(self, record):
            bucket = int(record.timestamp or 0) // (self.dedupe_window_seconds * 1000)
            return (
                f"{record.kind}:{record.server_id}:{record.backend_server}:"
                f"{bucket}:{record.content[:160]}"
            )

    def _observation_priority_score(record, matcher=None):
        if record.kind != "SERVER_LOG":
            return 0.0
        text = f"{record.content} {' '.join(record.tags)}".lower()
        if any(
            marker in text
            for marker in (
                "loop_suppressed",
                "fatal",
                "severe",
                "error",
                "exception",
                "failed",
                "timeout",
                "warn",
                "warning",
                "ban",
                "kick",
                "mute",
                "report",
                "spam",
                "grief",
                "cheat",
            )
        ):
            return 5.0
        return 1.0

    native_stub.ObservationRecordCodec = _ObservationRecordCodec
    native_stub.observation_priority_score = _observation_priority_score
    sys.modules["mine_sentinel_rs"] = native_stub

from services.mine_sentinel.models import MineSentinelConfig, ObservationRecord
from services.mine_sentinel.reporting.rules import HeuristicReportBuilder
from services.mine_sentinel.runtime_log import (
    MineSentinelRuntimeLogTailer,
    _build_observation,
    _resolve_log_file,
    _logs_dir,
    build_hour_observations,
    read_hour_log_lines,
)
from services.mine_sentinel.storage import DiskObservationStore
from services.mine_sentinel.hourly_summary import (
    HourlySummary,
    HourlySummaryStore,
    HourlySummarizer,
    format_cycle_report,
)
from services.mine_sentinel.jobs import HourlySummaryJob
from handlers.mine_sentinel_commands import parse_report_args, parse_window_minutes


class MineSentinelRuntimeLogAuditTests(unittest.TestCase):
    def test_report_command_window_parsing(self):
        self.assertEqual(parse_window_minutes("8h"), 480)
        self.assertEqual(parse_window_minutes("30m"), 30)
        self.assertEqual(parse_window_minutes("15min"), 15)
        self.assertIsNone(parse_window_minutes("survival"))

        target = parse_report_args(["survival", "8h"])

        self.assertEqual(target.server_id, "survival")
        self.assertEqual(target.window_minutes, 480)

    def test_runtime_log_config_parses_root_source(self):
        config = MineSentinelConfig.from_dict(
            {
                "runtime_log": {
                    "sources": [
                        {
                            "server_id": "survival",
                            "server_name": "Survival",
                            "root": "D:\\minecraftserver",
                        }
                    ],
                    "backfill_window_minutes": 480,
                    "loop_filter_enabled": True,
                }
            }
        )

        self.assertTrue(config.runtime_log.enabled)
        self.assertEqual(config.runtime_log.sources[0].server_id, "survival")
        self.assertEqual(config.runtime_log.sources[0].root, "D:\\minecraftserver")
        self.assertEqual(config.runtime_log.backfill_window_minutes, 480)
        self.assertTrue(config.runtime_log.loop_filter_enabled)

    def test_store_accepts_server_logs_only(self):
        config = MineSentinelConfig.from_dict({})
        now = int(time.time() * 1000)
        payload = {
            "serverId": "survival",
            "observations": [
                {
                    "eventId": "chat-1",
                    "kind": "CHAT",
                    "timestamp": now,
                    "serverId": "survival",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "hello",
                },
                {
                    "eventId": "non-log-1",
                    "kind": "PLAYER_EVENT",
                    "timestamp": now,
                    "serverId": "survival",
                    "content": "Steve joined the game",
                },
                {
                    "eventId": "log-1",
                    "kind": "SERVER_LOG",
                    "timestamp": now,
                    "serverId": "survival",
                    "content": "[Server thread/ERROR]: Test plugin failed",
                    "tags": ["server_log", "error"],
                    "context": {"level": "ERROR"},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = DiskObservationStore(config, Path(tmp_dir))
            written = store.add_batch("survival", payload)
            records = store.recent(60, "survival")

        self.assertEqual(written, 1)
        self.assertEqual([record.kind for record in records], ["SERVER_LOG"])

    def test_backfill_reads_compressed_logs_and_filters_error_loop(self):
        asyncio.run(self._run_backfill_flow())

    async def _run_backfill_flow(self):
        _install_astrbot_stubs()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "server"
            logs_dir = root / "logs"
            logs_dir.mkdir(parents=True)

            now = time.localtime()
            date_text = time.strftime("%Y-%m-%d", now)
            time_text = time.strftime("%H:%M:%S", now)
            archive = logs_dir / f"{date_text}-1.log.gz"
            repeated = [
                f"[{time_text} ERROR]: Failed to tick plugin Example id={idx}"
                for idx in range(8)
            ]
            with gzip.open(archive, "wt", encoding="utf-8") as handle:
                handle.write("\n".join(repeated))
                handle.write("\n")
            (logs_dir / "latest.log").write_text(
                f"[{time_text} INFO]: Server started\n",
                encoding="utf-8",
            )

            config = MineSentinelConfig.from_dict(
                {
                    "runtime_log": {
                        "sources": [
                            {
                                "server_id": "survival",
                                "server_name": "Survival",
                                "root": str(root),
                            }
                        ],
                        "backfill_window_minutes": 480,
                        "loop_filter_window_seconds": 300,
                        "loop_summary_interval_seconds": 1,
                    }
                }
            )
            batches = []

            async def handle_batch(server_id, payload):
                batches.append((server_id, payload))

            tailer = MineSentinelRuntimeLogTailer(
                config.runtime_log,
                handle_batch,
                io_runner=_run_sync,
            )
            state = types.SimpleNamespace(source=config.runtime_log.sources[0])
            await tailer._backfill_source(state, config.runtime_log.backfill_window_minutes)

            observations = [
                item
                for _server_id, payload in batches
                for item in payload.get("observations", [])
            ]

        self.assertTrue(observations)
        self.assertTrue(any(item["kind"] == "SERVER_LOG" for item in observations))
        self.assertTrue(any(item["context"]["compressed"] for item in observations))
        self.assertTrue(
            any("loop_suppressed" in item.get("tags", []) for item in observations)
        )
        self.assertLess(len(observations), len(repeated))

    def test_report_builder_summarizes_server_log_errors(self):
        now = int(time.time() * 1000)
        records = [
            ObservationRecord(
                event_id="log-1",
                kind="SERVER_LOG",
                timestamp=now,
                server_id="survival",
                server_name="Survival",
                content="[Server thread/ERROR]: Failed to load datapack",
                tags=["server_log", "runtime_log", "error"],
                context={"level": "ERROR"},
            ),
            ObservationRecord(
                event_id="log-2",
                kind="SERVER_LOG",
                timestamp=now + 1000,
                server_id="survival",
                server_name="Survival",
                content="同类服务器报错已合并：7 条重复日志被过滤；首条样本：Failed to load datapack",
                tags=["server_log", "runtime_log", "loop_suppressed", "error"],
                context={"level": "ERROR", "loopSuppressed": 7},
            ),
        ]

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            480,
            "survival",
        )

        self.assertEqual(report["log_count"], 2)
        self.assertTrue(any(issue["category"] == "bug" for issue in report["issues"]))
        self.assertTrue(any("重复服务器报错循环日志" in note for note in report["ops_notes"]))

    def test_report_builder_splits_community_management(self):
        now = int(time.time() * 1000)
        records = [
            ObservationRecord(
                event_id="log-community",
                kind="SERVER_LOG",
                timestamp=now,
                server_id="survival",
                server_name="Survival",
                content="[Server thread/WARN]: Player Steve was muted for spam",
                tags=["server_log", "runtime_log", "warning"],
                context={"level": "WARN"},
            )
        ]

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            480,
            "survival",
        )

        self.assertTrue(report["categories"]["community"])
        self.assertTrue(any(issue["category"] == "community" for issue in report["issues"]))

    def test_log_source_supports_logs_dir_and_server_type(self):
        config = MineSentinelConfig.from_dict(
            {
                "runtime_log": {
                    "sources": [
                        {
                            "server_id": "proxy",
                            "server_name": "Velocity 代理",
                            "server_type": "velocity",
                            "logs_dir": "/opt/velocity/logs",
                        },
                        {
                            "server_id": "survival",
                            "server_type": "paper",
                            "root": "/opt/paper",
                        },
                        {
                            "server_id": "creative",
                            "log_file": "/opt/creative/logs/latest.log",
                        },
                    ]
                }
            }
        )

        sources = config.runtime_log.sources
        self.assertEqual(len(sources), 3)

        proxy = sources[0]
        self.assertEqual(proxy.server_type, "velocity")
        self.assertEqual(proxy.logs_dir, "/opt/velocity/logs")
        self.assertEqual(
            _resolve_log_file(proxy),
            Path("/opt/velocity/logs/latest.log"),
        )
        self.assertEqual(_logs_dir(proxy), Path("/opt/velocity/logs"))

        survival = sources[1]
        self.assertEqual(survival.server_type, "minecraft")  # paper 归一为 minecraft
        self.assertEqual(
            _resolve_log_file(survival),
            Path("/opt/paper/logs/latest.log"),
        )

        creative = sources[2]
        self.assertEqual(creative.server_type, "minecraft")
        self.assertEqual(
            _resolve_log_file(creative),
            Path("/opt/creative/logs/latest.log"),
        )

    def test_resolve_log_file_prefers_log_file_over_logs_dir_and_root(self):
        from services.mine_sentinel.models import MineSentinelLogSourceConfig

        source = MineSentinelLogSourceConfig(
            root="/opt/paper",
            logs_dir="/var/log/paper",
            log_file="/tmp/explicit.log",
        )
        self.assertEqual(_resolve_log_file(source), Path("/tmp/explicit.log"))
        self.assertEqual(_logs_dir(source), Path("/var/log/paper"))

        source_no_logfile = MineSentinelLogSourceConfig(
            root="/opt/paper",
            logs_dir="/var/log/paper",
        )
        self.assertEqual(
            _resolve_log_file(source_no_logfile),
            Path("/var/log/paper/latest.log"),
        )

        source_only_root = MineSentinelLogSourceConfig(root="/opt/paper")
        self.assertEqual(
            _resolve_log_file(source_only_root),
            Path("/opt/paper/logs/latest.log"),
        )

    def test_build_observation_marks_velocity_proxy_tag(self):
        from services.mine_sentinel.models import MineSentinelLogSourceConfig

        proxy_source = MineSentinelLogSourceConfig(
            server_id="proxy",
            server_name="Velocity",
            server_type="velocity",
        )
        observation = _build_observation(
            proxy_source,
            Path("/opt/velocity/logs/latest.log"),
            "[12:00:00 INFO]: [connected player] Bob -> survival",
            1700000000000,
            1000,
        )
        self.assertIn("velocity", observation["tags"])
        self.assertIn("proxy", observation["tags"])
        self.assertEqual(observation["context"]["serverType"], "velocity")

        mc_source = MineSentinelLogSourceConfig(
            server_id="survival",
            server_name="Survival",
            server_type="minecraft",
        )
        observation_mc = _build_observation(
            mc_source,
            Path("/opt/paper/logs/latest.log"),
            "[12:00:00 INFO]: Done!",
            1700000000000,
            1000,
        )
        self.assertIn("minecraft", observation_mc["tags"])
        self.assertNotIn("velocity", observation_mc["tags"])
        self.assertEqual(observation_mc["context"]["serverType"], "minecraft")

    def test_start_warns_when_no_sources_configured(self):
        captured = []

        class CaptureLogger:
            def warning(self, msg, *args, **kwargs):
                captured.append(msg)

            def info(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

            def debug(self, *args, **kwargs):
                pass

        import services.mine_sentinel.runtime_log as runtime_log_module

        original_logger = runtime_log_module.logger
        runtime_log_module.logger = CaptureLogger()
        try:
            config = MineSentinelConfig.from_dict({"runtime_log": {"enabled": True}})
            tailer = MineSentinelRuntimeLogTailer(
                config.runtime_log,
                batch_handler=lambda *a, **kw: None,
                io_runner=_run_sync,
            )
            tailer.start()
        finally:
            runtime_log_module.logger = original_logger

        self.assertTrue(captured)
        self.assertTrue(
            any("未配置任何 Minecraft 运行日志源" in msg for msg in captured),
            f"expected no-sources warning, got: {captured}",
        )


class MineSentinelHourlySummaryTests(unittest.IsolatedAsyncioTestCase):
    """Tests for the hourly summary mode (no polling, per-hour log read + AI integrate)."""

    def setUp(self):
        _install_astrbot_stubs()

    def _make_log_dir(self, tmp: Path, lines: list[tuple[str, str]]) -> Path:
        """Create a logs/ dir under tmp with latest.log and one .log.gz archive.

        Each entry is (date_or_time, line); we just write the line verbatim.
        """
        logs = tmp / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "latest.log").write_text(
            "\n".join(line for _, line in lines) + "\n", encoding="utf-8"
        )
        return tmp

    def test_read_hour_log_lines_filters_by_timestamp(self):
        from datetime import datetime, timedelta
        from services.mine_sentinel.models import MineSentinelLogSourceConfig

        # Use the current wall-clock hour so the timestamp parser's
        # "future time -> subtract 24h" heuristic doesn't kick in.
        now = datetime.now()
        cur_hour = now.replace(minute=0, second=0, microsecond=0)
        prev_hour = cur_hour - timedelta(hours=1)
        hour_a_start_ms = int(prev_hour.timestamp() * 1000)
        hour_b_start_ms = int(cur_hour.timestamp() * 1000)
        hour_a_end_ms = hour_b_start_ms
        lines = [
            f"[{prev_hour:%H:%M:%S}] [Server thread/INFO]: hour A line 1",
            f"[{prev_hour + timedelta(minutes=30):%H:%M:%S}] [Server thread/INFO]: hour A line 2",
            f"[{cur_hour:%H:%M:%S}] [Server thread/INFO]: hour B line 1",
            f"[{cur_hour + timedelta(minutes=45):%H:%M:%S}] [Server thread/INFO]: hour B line 2",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._make_log_dir(tmp_path, [(0, line) for line in lines])

            source = MineSentinelLogSourceConfig(
                server_id="srv",
                server_name="Srv",
                server_type="minecraft",
                root=str(tmp_path),
            )
            rows = read_hour_log_lines(source, hour_a_start_ms, hour_a_end_ms)
            self.assertEqual(len(rows), 2)
            for line, ts, _path in rows:
                self.assertIn("hour A", line)

            rows_b = read_hour_log_lines(
                source, hour_b_start_ms, hour_b_start_ms + 3600 * 1000
            )
            self.assertEqual(len(rows_b), 2)
            for line, ts, _path in rows_b:
                self.assertIn("hour B", line)

    def test_build_hour_observations_returns_observation_dicts(self):
        from datetime import datetime, timedelta
        from services.mine_sentinel.models import MineSentinelLogSourceConfig

        now = datetime.now()
        cur_hour = now.replace(minute=0, second=0, microsecond=0)
        prev_hour = cur_hour - timedelta(hours=1)
        hour_start_ms = int(prev_hour.timestamp() * 1000)
        hour_end_ms = int(cur_hour.timestamp() * 1000)
        lines = [
            f"[{prev_hour:%H:%M:%S}] [Server thread/INFO]: Done (3.5s)! For help, type help",
            f"[{prev_hour + timedelta(minutes=10):%H:%M:%S}] [Server thread/WARN]: Can't keep up!",
            f"[{prev_hour + timedelta(minutes=20):%H:%M:%S}] [Server thread/ERROR]: Exception ticking entity",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._make_log_dir(tmp_path, [(0, line) for line in lines])
            source = MineSentinelLogSourceConfig(
                server_id="srv",
                server_name="Srv",
                server_type="minecraft",
                root=str(tmp_path),
            )
            observations = build_hour_observations(
                source, hour_start_ms, hour_end_ms, max_records=10
            )
            self.assertEqual(len(observations), 3)
            for obs in observations:
                self.assertEqual(obs["kind"], "SERVER_LOG")
                self.assertEqual(obs["serverId"], "srv")
                self.assertEqual(obs["context"]["serverType"], "minecraft")
                self.assertEqual(
                    obs["context"]["source"], "astrbot_hourly_read"
                )
            self.assertTrue(any("error" in o["tags"] for o in observations))
            self.assertTrue(any("warning" in o["tags"] for o in observations))

    def test_hourly_summary_store_save_load_and_list_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HourlySummaryStore(Path(tmp))
            hs = HourlySummary(
                server_id="srv",
                server_name="Srv",
                hour_start_ms=1700000000000,
                hour_end_ms=1700003600000,
                records_count=10,
                error_count=1,
                warning_count=2,
                info_count=7,
                summary="小时总结",
                key_issues=[{"title": "x", "severity": "high"}],
                top_events=["e1", "e2"],
                source="ai",
            )
            path = store.save(hs)
            self.assertTrue(path.exists())

            loaded = store.load("srv", hs.hour_start_ms)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.records_count, 10)
            self.assertEqual(loaded.summary, "小时总结")

            cycle_summaries = store.list_cycle_summaries(
                "srv",
                hs.hour_start_ms,
                hs.hour_end_ms + 3600 * 1000,
            )
            self.assertEqual(len(cycle_summaries), 1)
            self.assertEqual(cycle_summaries[0].server_id, "srv")

    def test_hourly_summarizer_falls_back_to_heuristic_without_provider(self):
        from services.mine_sentinel.models import MineSentinelLogSourceConfig

        config = MineSentinelConfig.from_dict({})
        summarizer = HourlySummarizer(config, context=None)
        records = [
            ObservationRecord(
                event_id="srv:1",
                kind="SERVER_LOG",
                timestamp=1700000000000,
                server_id="srv",
                content="[14:00:00] [Server thread/INFO]: Done",
                tags=["server_log", "runtime_log", "info", "minecraft"],
                context={"serverType": "minecraft"},
            ),
            ObservationRecord(
                event_id="srv:2",
                kind="SERVER_LOG",
                timestamp=1700000100000,
                server_id="srv",
                content="[14:10:00] [Server thread/ERROR]: boom",
                tags=["server_log", "runtime_log", "error", "exception", "minecraft"],
                context={"serverType": "minecraft"},
            ),
        ]
        source = MineSentinelLogSourceConfig(
            server_id="srv", server_name="Srv", server_type="minecraft"
        )
        hourly = asyncio.get_event_loop().run_until_complete(
            summarizer.build_hourly_summary(
                records, source, 1700000000000, 1700003600000, umo=None
            )
        )
        self.assertEqual(hourly.source, "heuristic")
        self.assertEqual(hourly.records_count, 2)
        self.assertEqual(hourly.error_count, 1)
        self.assertGreater(len(hourly.summary), 0)

    def test_cycle_report_heuristic_integrates_hourly_summaries(self):
        config = MineSentinelConfig.from_dict({})
        summarizer = HourlySummarizer(config, context=None)
        summaries = [
            HourlySummary(
                server_id="srv",
                server_name="Srv",
                hour_start_ms=1700000000000 + i * 3600000,
                hour_end_ms=1700000000000 + (i + 1) * 3600000,
                records_count=10 + i,
                error_count=i,
                warning_count=2,
                info_count=8 - i,
                summary=f"第 {i+1} 小时总结",
                key_issues=[{"title": f"issue-{i}", "severity": "high"}],
                top_events=[f"event-{i}"],
                source="heuristic",
            )
            for i in range(8)
        ]
        report = asyncio.get_event_loop().run_until_complete(
            summarizer.build_cycle_report(summaries, "srv", umo=None)
        )
        self.assertEqual(report["source"], "heuristic")
        self.assertEqual(report["total_records"], sum(10 + i for i in range(8)))
        self.assertEqual(report["total_errors"], sum(range(8)))
        self.assertEqual(len(report["timeline"]), 8)

        text = format_cycle_report(report, summaries, "Srv")
        self.assertIn("MineSentinel 周期报告", text)
        self.assertIn("8 小时", text)
        self.assertIn("第 1 小时总结", text)

    def test_hourly_summary_job_seconds_until_next_hour_aligns_to_wall_clock(self):
        # 14:35:00 -> next hour at 15:00:00 = 1500 seconds.
        next_hour = HourlySummaryJob.seconds_until_next_hour(
            time.mktime(time.strptime("2026-07-05 14:35:00", "%Y-%m-%d %H:%M:%S"))
        )
        self.assertAlmostEqual(next_hour, 1500.0, delta=1.0)
        # 14:00:30 -> next hour at 15:00:00 = 3570 seconds.
        next_hour2 = HourlySummaryJob.seconds_until_next_hour(
            time.mktime(time.strptime("2026-07-05 14:00:30", "%Y-%m-%d %H:%M:%S"))
        )
        self.assertAlmostEqual(next_hour2, 3570.0, delta=1.0)

    def test_config_parses_hourly_summary_section(self):
        config = MineSentinelConfig.from_dict(
            {
                "hourly_summary": {
                    "enabled": True,
                    "hours_per_cycle": 4,
                    "window_minutes": 60,
                    "poll_enabled": False,
                    "provider_id": "openai:gpt-4",
                    "max_records_per_hour": 1000,
                    "max_log_lines_per_hour": 5000,
                    "retention_cycles": 3,
                }
            }
        )
        self.assertTrue(config.hourly_summary.enabled)
        self.assertEqual(config.hourly_summary.hours_per_cycle, 4)
        self.assertFalse(config.hourly_summary.poll_enabled)
        self.assertEqual(config.hourly_summary.provider_id, "openai:gpt-4")
        self.assertEqual(config.hourly_summary.max_records_per_hour, 1000)
        self.assertEqual(config.hourly_summary.retention_cycles, 3)

    async def test_service_hourly_mode_skips_polling(self):
        """When hourly_summary.enabled is True and poll_enabled is False,
        the runtime_log_tailer must NOT be started."""
        import services.mine_sentinel.service as service_module
        from services.mine_sentinel.service import MineSentinelService

        with tempfile.TemporaryDirectory() as tmp:
            config_data = {
                "enabled": True,
                "runtime_log": {
                    "enabled": True,
                    "sources": [
                        {
                            "server_id": "srv",
                            "server_name": "Srv",
                            "server_type": "minecraft",
                            "root": tmp,
                        }
                    ],
                },
                "hourly_summary": {
                    "enabled": True,
                    "poll_enabled": False,
                    "hours_per_cycle": 8,
                },
                "report": {"enabled": False},
            }

            class _FakeContext:
                pass

            service = MineSentinelService(
                context=_FakeContext(),
                config_data=config_data,
                get_server_config=lambda sid: None,
                storage_dir=tmp,
                io_runner=_run_sync,
            )

            tailer_started = False
            original_start = service.runtime_log_tailer.start

            def _spy_start():
                nonlocal tailer_started
                tailer_started = True
                original_start()

            service.runtime_log_tailer.start = _spy_start

            try:
                service.start()
            finally:
                await service.stop()

            self.assertFalse(
                tailer_started,
                "runtime_log_tailer should NOT start when hourly mode is on and poll_enabled is false",
            )

    async def test_service_hourly_calls_run_hour_per_source(self):
        """The HourlySummaryJob should invoke _run_hourly_for_source once per source."""
        from services.mine_sentinel.service import MineSentinelService

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logs = tmp_path / "logs"
            logs.mkdir(parents=True)
            (logs / "latest.log").write_text(
                "[14:00:00] [Server thread/INFO]: hello\n", encoding="utf-8"
            )
            config_data = {
                "enabled": True,
                "runtime_log": {
                    "enabled": True,
                    "sources": [
                        {
                            "server_id": "srv",
                            "server_name": "Srv",
                            "server_type": "minecraft",
                            "root": str(tmp_path),
                        }
                    ],
                },
                "hourly_summary": {
                    "enabled": True,
                    "poll_enabled": False,
                    "hours_per_cycle": 8,
                },
                "report": {"enabled": False},
            }

            class _FakeContext:
                pass

            service = MineSentinelService(
                context=_FakeContext(),
                config_data=config_data,
                get_server_config=lambda sid: None,
                storage_dir=tmp,
                io_runner=_run_sync,
            )

            called: list[tuple[int, int, str]] = []

            async def _spy_run_hour(h_start, h_end, sid):
                called.append((h_start, h_end, sid))

            service._run_hourly_for_source = _spy_run_hour
            # Rebind the job's run_hour to our spy since it was captured at construction.
            service._hourly_job.run_hour = _spy_run_hour

            # Manually invoke the partial-hour handler as if the job just started.
            await service._hourly_job._process_current_partial_hour()

            self.assertEqual(len(called), 1)
            self.assertEqual(called[0][2], "srv")


async def _run_sync(func, *args):
    return func(*args)


def _install_astrbot_stubs():
    class Logger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
    api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
    api.logger = getattr(api, "logger", Logger())
    api.__path__ = []  # mark as package so `from astrbot.api.X import Y` works
    # Stub astrbot.api.event so service -> delivery can import MessageChain.
    if "astrbot.api.event" not in sys.modules:
        event_mod = types.ModuleType("astrbot.api.event")

        class _MessageChain:
            def __init__(self, nodes=None):
                self.nodes = list(nodes or [])

        event_mod.MessageChain = _MessageChain
        sys.modules["astrbot.api.event"] = event_mod
    # Stub astrbot.api.message_components for Plain etc.
    if "astrbot.api.message_components" not in sys.modules:
        comp_mod = types.ModuleType("astrbot.api.message_components")

        class _Plain:
            def __init__(self, text=""):
                self.text = str(text)

        comp_mod.Plain = _Plain
        sys.modules["astrbot.api.message_components"] = comp_mod
    sys.modules.update({"astrbot": astrbot, "astrbot.api": api})


if __name__ == "__main__":
    unittest.main()
