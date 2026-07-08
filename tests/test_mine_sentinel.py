from __future__ import annotations

import json
import asyncio
import datetime as dt
import re
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.alerts import (
        MineSentinelAlertEngine,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.formatter import (
        format_report,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.models import (
        MineSentinelConfig,
        ObservationRecord,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.console_log import (
        parse_minecraft_console_log,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.observation_priority import (
        observation_priority_score,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.rules import (
        HeuristicReportBuilder,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        rules as reporting_rules_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        text_renderer as text_renderer_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        image_renderer as image_renderer_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.common import (
        record_location,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue import (
        PlayerDialogueAnalyzer,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        dialogue_evidence as dialogue_evidence_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue_context import (
        is_continuation_message,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue_output import (
        DialogueIssueBuilder,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue_scoring import (
        dialogue_score,
        dialogue_severity,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue_rules import (
        DIALOGUE_RULES,
        custom_dialogue_rules,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue_signals import (
        DialogueSignalCollector,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.dialogue_terms import (
        DialogueRuleMatcher,
        matched_terms,
        message_fingerprint,
        normalize_text,
        term_is_negated,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.metrics_context import (
        build_metric_context,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.incidents import (
        IncidentGrouper,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        incidents as incidents_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.ai_normalizer import (
        AIReportNormalizer,
        parse_json_object,
        repair_json_object_text,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.ai_prompt import (
        AIReportPromptBuilder,
        compact_evidence_sample,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        ai_prompt as ai_prompt_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.player_refs import (
        mentioned_players,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.sampling import (
        even_sample,
        sample_records_for_ai,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting import (
        sampling as sampling_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.routing import (
        MineSentinelTargetRouter,
        normalize_delivery_target,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.storage import (
        DedupeTracker,
        DiskObservationStore,
        RecentObservationWindow,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.storage.codec import (
        ObservationRecordCodec,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.storage import (
        codec as storage_codec_module,
    )
    from astrbot_plugin_minecraft_adapter.services.mine_sentinel.storage.window import (
        RecentWindowBuilder,
    )
except ModuleNotFoundError:
    from services.mine_sentinel.alerts import MineSentinelAlertEngine
    from services.mine_sentinel.console_log import parse_minecraft_console_log
    from services.mine_sentinel.formatter import format_report
    from services.mine_sentinel.models import MineSentinelConfig, ObservationRecord
    from services.mine_sentinel.observation_priority import observation_priority_score
    from services.mine_sentinel.reporting.dialogue import PlayerDialogueAnalyzer
    from services.mine_sentinel.reporting import dialogue_evidence as dialogue_evidence_module
    from services.mine_sentinel.reporting.dialogue_context import is_continuation_message
    from services.mine_sentinel.reporting.dialogue_output import (
        DialogueIssueBuilder,
    )
    from services.mine_sentinel.reporting.dialogue_scoring import (
        dialogue_score,
        dialogue_severity,
    )
    from services.mine_sentinel.reporting.dialogue_rules import DIALOGUE_RULES
    from services.mine_sentinel.reporting.dialogue_rules import custom_dialogue_rules
    from services.mine_sentinel.reporting.dialogue_signals import DialogueSignalCollector
    from services.mine_sentinel.reporting.dialogue_terms import (
        DialogueRuleMatcher,
        matched_terms,
        message_fingerprint,
        normalize_text,
        term_is_negated,
    )
    from services.mine_sentinel.reporting.ai_normalizer import (
        AIReportNormalizer,
        parse_json_object,
        repair_json_object_text,
    )
    from services.mine_sentinel.reporting.ai_prompt import AIReportPromptBuilder
    from services.mine_sentinel.reporting.ai_prompt import compact_evidence_sample
    from services.mine_sentinel.reporting import ai_prompt as ai_prompt_module
    from services.mine_sentinel.reporting.incidents import IncidentGrouper
    from services.mine_sentinel.reporting import incidents as incidents_module
    from services.mine_sentinel.reporting.metrics_context import build_metric_context
    from services.mine_sentinel.reporting.player_refs import mentioned_players
    from services.mine_sentinel.reporting.rules import HeuristicReportBuilder
    from services.mine_sentinel.reporting import rules as reporting_rules_module
    from services.mine_sentinel.reporting import text_renderer as text_renderer_module
    from services.mine_sentinel.reporting import image_renderer as image_renderer_module
    from services.mine_sentinel.reporting.common import record_location
    from services.mine_sentinel.reporting.sampling import even_sample, sample_records_for_ai
    from services.mine_sentinel.reporting import sampling as sampling_module
    from services.mine_sentinel.routing import (
        MineSentinelTargetRouter,
        normalize_delivery_target,
    )
    from services.mine_sentinel.storage import (
        DedupeTracker,
        DiskObservationStore,
        RecentObservationWindow,
    )
    from services.mine_sentinel.storage.codec import ObservationRecordCodec
    from services.mine_sentinel.storage import codec as storage_codec_module
    from services.mine_sentinel.storage.window import RecentWindowBuilder


class MineSentinelDialogueTests(unittest.TestCase):
    def test_dialogue_term_helpers_are_negation_scoped(self):
        self.assertTrue(term_is_negated("今天不卡", "卡"))
        self.assertFalse(term_is_negated("不卡但是一直掉线", "掉线"))
        self.assertEqual(matched_terms("不卡但是一直掉线", ("卡", "掉线")), ["掉线"])

    def test_message_fingerprint_ignores_chat_noise(self):
        self.assertEqual(
            message_fingerprint(" 服务器卡到玩不了!!! "),
            message_fingerprint("服务器 卡到 玩不了..."),
        )
        self.assertEqual(message_fingerprint("卡卡卡卡"), "卡卡")

    def test_dialogue_matcher_sparse_candidates_keep_rule_order(self):
        rules = custom_dialogue_rules(
            [
                {"tag": "alpha", "keywords": ["aaa"]},
                {"tag": "beta", "keywords": ["bbb"]},
                *[
                    {"tag": f"noise_{index}", "keywords": [f"zzz_{index}"]}
                    for index in range(20)
                ],
            ]
        )
        matcher = DialogueRuleMatcher(rules)

        matches = matcher.direct_matches("bbb aaa")

        self.assertEqual(
            [rule.tag for rule, _terms in matches],
            ["custom_alpha", "custom_beta"],
        )

    def test_player_reference_helper_filters_noise_and_speaker(self):
        players = mentioned_players("Alice 说 @Bob lag，不是 tps", speaker="Alice")

        self.assertEqual(players, ["Bob"])

    def test_ai_sampling_prioritizes_issue_dialogue(self):
        now = int(time.time() * 1000)
        records = [
            self._metric(now - 9000, "19.9"),
            self._chat(now - 8000, "Neutral1", "今天大家在建房子"),
            self._chat(now - 7000, "Alice", "服务器卡到动不了"),
            self._chat(now - 6000, "Neutral2", "有人要木头吗"),
            self._chat(now - 5000, "Bob", "我也卡，延迟很高"),
            self._metric(now - 4000, "20.0"),
            self._chat(now - 3000, "Neutral3", "晚上打龙吗"),
        ]
        fallback = {
            "issues": [
                {
                    "tag": "performance_lag",
                    "severity": "high",
                    "players": ["Alice"],
                    "mentioned_players": ["Bob"],
                    "dialogue_terms": ["卡", "延迟"],
                    "evidence_samples": [records[2].evidence_text()],
                }
            ]
        }

        sampled = sample_records_for_ai(records, 3, fallback)
        sampled_names = [record.player_name for record in sampled if record.kind == "CHAT"]

        self.assertIn("Alice", sampled_names)
        self.assertIn("Bob", sampled_names)
        self.assertLessEqual(len(sampled), 3)

    def test_ai_record_score_uses_indexed_evidence_blob(self):
        outer = self

        class NonIterableEvidence(set):
            def __iter__(self):
                outer.fail("record scoring should not iterate evidence samples")

        record = ObservationRecord(
            kind="CHAT",
            content="target lag message",
            tags=["chat"],
        )
        focus = {
            "players": set(),
            "terms": set(),
            "evidence": NonIterableEvidence({"target lag message"}),
            "evidence_blob": "context before\0target lag message\0context after",
        }

        self.assertEqual(sampling_module._record_score(record, focus), 9.0)

    def test_ai_prompt_builder_bounds_prompt_size(self):
        now = int(time.time() * 1000)
        config = MineSentinelConfig.from_dict(
            {
                "report": {
                    "max_ai_prompt_chars": 700,
                    "max_ai_records": 4,
                    "max_ai_content_length": 20,
                }
            }
        )
        records = [
            self._chat(now - idx * 1000, f"Player{idx}", "服务器卡到玩不了" * 20)
            for idx in range(20)
        ]
        fallback = self._fallback_report(
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "players": ["Player1", "Player2"],
                "mentioned_players": [],
                "affected_locations": ["survival/s1"],
                "evidence_samples": ["服务器卡到玩不了" * 40],
                "evidence_count": 20,
                "signal_count": 20,
                "unique_players": 20,
                "suggested_action": "检查 TPS。",
            }
        )

        prompt = AIReportPromptBuilder(config).build(records, 480, fallback)

        self.assertLessEqual(len(prompt), 700)
        self.assertIn("JSON", prompt)

    def test_ai_prompt_compact_fallback_projects_issue_fields(self):
        builder = AIReportPromptBuilder(MineSentinelConfig.from_dict({}))
        fallback = self._fallback_report(
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "players": ["Alice"],
                "players_text": "Alice",
                "affected_locations": ["survival/s1"],
                "affected_locations_text": "survival/s1",
                "metric_context_text": "survival/s1 TPS 12.4",
                "metric_context": {"raw_samples": ["x" * 1000]},
                "debug_payload": {"raw": "y" * 1000},
                "evidence_samples": ["target evidence " + "z" * 1000],
                "evidence_count": 1,
                "signal_count": 1,
                "unique_players": 1,
                "suggested_action": "Check TPS.",
            }
        )

        compact = builder.compact_fallback(fallback)
        issue = compact["issues"][0]

        self.assertEqual(issue["metric_context_text"], "survival/s1 TPS 12.4")
        self.assertNotIn("metric_context", issue)
        self.assertNotIn("debug_payload", issue)
        self.assertNotIn("players_text", issue)
        self.assertNotIn("affected_locations_text", issue)
        self.assertLessEqual(len(issue["evidence_samples"][0]), 220)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(fallback)))

    def test_ai_prompt_fit_reuses_serialized_timeline_while_trimming_records(self):
        builder = AIReportPromptBuilder(
            MineSentinelConfig.from_dict({"report": {"max_ai_prompt_chars": 10}})
        )
        chunks = [
            {
                "start_ts": 1,
                "end_ts": 2,
                "count": 10,
                "samples": [],
            }
        ]
        records = [
            {
                "kind": "CHAT",
                "content": "x" * 120,
                "timestamp": index,
            }
            for index in range(10)
        ]
        calls = {"chunks": 0, "records": 0}
        original = ai_prompt_module.compact_json

        def counted_compact_json(value):
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if "start_ts" in value[0]:
                    calls["chunks"] += 1
                elif "content" in value[0]:
                    calls["records"] += 1
            return original(value)

        ai_prompt_module.compact_json = counted_compact_json
        try:
            builder.fit_prompt(480, "{}", chunks, records)
        finally:
            ai_prompt_module.compact_json = original

        self.assertEqual(calls["chunks"], 1)
        self.assertGreater(calls["records"], 1)

    def test_report_config_reads_direct_delivery_targets(self):
        config = MineSentinelConfig.from_dict(
            {
                "report": {
                    "delivery_targets": [
                        "group:123456",
                        "qq:654321",
                        {"type": "group", "id": "777888"},
                    ]
                }
            }
        )

        self.assertEqual(
            config.report.delivery_targets,
            ["group:123456", "qq:654321", {"type": "group", "id": "777888"}],
        )

    def test_ai_prompt_compacts_multiline_context_without_losing_target(self):
        sample = "\n".join(
            [
                "上下文 survival/s1:",
                "  07-01 12:00 Alice: " + "前置聊天" * 40,
                "  07-01 12:01 Bob: 有点奇怪",
                "> 07-01 12:02 Carol: 服务器卡到玩不了",
                "  07-01 12:03 Dave: 我也是",
                "  07-01 12:04 Erin: 后续补充",
            ]
        )

        compact = compact_evidence_sample(sample)

        self.assertLessEqual(len(compact), 520)
        self.assertIn("上下文 survival/s1", compact)
        self.assertIn("> 07-01 12:02 Carol: 服务器卡到玩不了", compact)
        self.assertIn("Dave: 我也是", compact)

    def test_ai_json_repair_extracts_object(self):
        raw = "```json\n{\"summary\":\"ok\"}\n```"

        repaired = repair_json_object_text(raw)

        self.assertEqual(parse_json_object(repaired), {"summary": "ok"})
        self.assertIsNone(parse_json_object("[]"))

    def test_observation_priority_scores_actionable_chat(self):
        now = int(time.time() * 1000)
        neutral = self._chat(now - 1000, "Alice", "今天在修房子")
        issue = self._chat(now, "Bob", "我的装备没了，能恢复吗")

        self.assertGreater(
            observation_priority_score(issue),
            observation_priority_score(neutral),
        )

    def test_observation_priority_scores_java_memory_pressure(self):
        now = int(time.time() * 1000)
        normal = ObservationRecord(
            event_id="metric-normal",
            kind="SERVER_METRICS",
            timestamp=now - 1000,
            server_id="survival",
            metrics={"memoryUsedMb": 1024, "memoryMaxMb": 4096},
        )
        high = ObservationRecord(
            event_id="metric-high",
            kind="SERVER_METRICS",
            timestamp=now,
            server_id="survival",
            metrics={"memoryUsedMb": 3900, "memoryMaxMb": 4096},
        )

        self.assertGreater(
            observation_priority_score(high),
            observation_priority_score(normal),
        )

    def test_even_sample_keeps_edges(self):
        self.assertEqual(even_sample(list(range(5)), 3), [0, 2, 4])

    def test_dialogue_report_keeps_actionable_player_findings(self):
        now = int(time.time() * 1000)
        records = [
            self._chat(now - 4000, "Alice", "服务器卡到动不了，玩不了"),
            self._chat(now - 3000, "Bob", "我也 lag，一直延迟"),
            self._chat(now - 2000, "Carol", "我的装备没了，能恢复吗"),
            self._chat(now - 1000, "Dave", "建议加个跨服提示"),
        ]

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            480,
            "survival",
        )

        self.assertGreaterEqual(len(report["dialogue_findings"]), 3)
        self.assertTrue(
            any("卡顿/延迟反馈" in item for item in report["dialogue_findings"])
        )
        self.assertTrue(
            any(issue["tag"] == "performance_lag" for issue in report["issues"])
        )
        self.assertIn("Alice", report["chat_players"])
        self.assertIn("Bob", report["chat_players"])

        text = format_report(report, len(records), 0, 4)
        self.assertIn("二、聊天与事件总结", text)
        self.assertIn("三、玩家问题/投诉识别", text)
        self.assertIn("Alice", text)
        self.assertIn("Carol", text)

    def test_mclogs_excerpt_renders_admin_five_section_summary(self):
        fixture = Path(__file__).parent / "fixtures" / "mclogs_pbfhCaI_excerpt.log"
        records = parse_minecraft_console_log(
            fixture.read_text(encoding="utf-8").splitlines(),
            server_id="spawn",
            server_name="Spawn",
            base_date=dt.date(2026, 7, 4),
        )

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            60,
            "spawn",
        )
        unique_players = len({record.identity for record in records if record.identity})
        text = format_report(report, len(records), 0, unique_players)
        issue_tags = {issue["tag"] for issue in report["issues"]}

        self.assertIn("server_security_warning", issue_tags)
        self.assertIn("slow_startup", issue_tags)
        self.assertIn("data_converter_error", issue_tags)
        self.assertIn("一、整体情况", text)
        self.assertIn("二、聊天与事件总结", text)
        self.assertIn("三、玩家问题/投诉识别", text)
        self.assertIn("四、风险提醒", text)
        self.assertIn("五、建议处理", text)
        self.assertIn("服务器安全配置风险", text)
        self.assertIn("启动耗时过长", text)
        self.assertIn("数据转换/NBT 错误", text)
        self.assertIn("运维排查", text)
        self.assertIn("管理员命令记录", text)

    def test_heuristic_report_reuses_dialogue_classification_cache(self):
        now = int(time.time() * 1000)
        records = [
            self._chat(now - 3000, "Alice", "server lag spike cannot move"),
            self._chat(now - 2000, "Bob", "lag is still bad"),
            self._chat(now - 1000, "Carol", "anyone else seeing lag"),
        ]
        builder = HeuristicReportBuilder(MineSentinelConfig.from_dict({}))

        def fail_if_recomputed(record):
            self.fail("matched_rule should not run during cached report bucketing")

        builder.dialogue.matched_rule = fail_if_recomputed
        report = builder.build(records, 30, "survival")

        self.assertTrue(
            any(issue["tag"] == "performance_lag" for issue in report["issues"])
        )

    def test_heuristic_report_aggregates_metrics_during_main_pass(self):
        now = int(time.time() * 1000)
        records = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "server lag spike"),
            self._chat_on_backend(now - 2000, "Bob", "s1", "lag is still bad"),
            self._chat_on_backend(now - 1000, "Carol", "s1", "anyone else lag"),
            self._metric_on_backend(now, "s1", 12.0, 91.0),
        ]
        original = reporting_rules_module.build_metric_context

        def fail_if_full_pass_runs(records):
            self.fail("build() should aggregate metrics during the main pass")

        reporting_rules_module.build_metric_context = fail_if_full_pass_runs
        try:
            report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
                records,
                30,
                "survival",
            )
        finally:
            reporting_rules_module.build_metric_context = original

        issue = next(
            issue for issue in report["issues"] if issue["tag"] == "performance_lag"
        )
        self.assertIn("metric_context_text", issue)
        self.assertTrue(report["ops_notes"])

    def test_heuristic_report_indexes_dialogue_issue_coverage(self):
        now = int(time.time() * 1000)
        records = [
            self._chat(now - 3000, "Alice", "server lag spike cannot move"),
            self._chat(now - 2000, "Bob", "lag is still bad"),
            self._chat(now - 1000, "Carol", "anyone else lag"),
        ]
        builder = HeuristicReportBuilder(MineSentinelConfig.from_dict({}))

        def fail_if_scanned(*args, **kwargs):
            self.fail("build() should use the precomputed dialogue issue key index")

        builder._has_dialogue_issue = fail_if_scanned
        report = builder.build(records, 30, "survival")

        self.assertTrue(
            any(issue["tag"] == "performance_lag" for issue in report["issues"])
        )

    def test_heuristic_report_build_uses_incremental_bucket_summaries(self):
        now = int(time.time() * 1000)
        records = [
            self._chat_on_backend(now, "Bob", "s2", "lag"),
            self._chat_on_backend(now + 1, "Alice", "s1", "lag"),
            self._chat_on_backend(now + 2, "Carol", "s1", "lag"),
        ]
        records[0].context = {"world": "world_nether"}
        builder = HeuristicReportBuilder(MineSentinelConfig.from_dict({}))
        original_group_summary = builder._group_summary

        def fail_if_group_rescanned(*args, **kwargs):
            self.fail("build() should use incremental bucket summaries")

        builder._group_summary = fail_if_group_rescanned
        try:
            report = builder.build(records, 30, "survival")
        finally:
            builder._group_summary = original_group_summary

        issue = next(
            issue for issue in report["issues"] if issue["tag"] == "performance_lag"
        )
        self.assertEqual(issue["players"], ["Alice", "Bob", "Carol"])
        self.assertIn("survival/s2@world_nether", issue["affected_locations"])

    def test_incident_grouper_indexes_scopes_added_by_later_merges(self):
        now = int(time.time() * 1000)
        issues = [
            {
                "category": "complaint",
                "tag": "proxy_lag",
                "severity": "high",
                "affected_servers": ["proxy-a"],
                "first_seen_ts": now,
                "last_seen_ts": now,
            },
            {
                "category": "complaint",
                "tag": "backend_lag",
                "severity": "high",
                "affected_servers": ["proxy-a"],
                "affected_locations": ["survival/s1"],
                "first_seen_ts": now + 1000,
                "last_seen_ts": now + 1000,
            },
            {
                "category": "complaint",
                "tag": "same_backend_followup",
                "severity": "high",
                "affected_locations": ["survival/s1"],
                "first_seen_ts": now + 2000,
                "last_seen_ts": now + 2000,
            },
        ]

        groups = IncidentGrouper().group(issues)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].issues), 3)
        self.assertIn("survival/s1", groups[0].scopes)

    def test_incident_grouper_reuses_precomputed_scopes_for_new_groups(self):
        now = int(time.time() * 1000)
        issues = [
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "affected_locations": ["survival/s1"],
                "first_seen_ts": now,
                "last_seen_ts": now,
            },
            {
                "category": "suggestion",
                "tag": "player_suggestion",
                "severity": "high",
                "affected_locations": ["survival/s2"],
                "first_seen_ts": now,
                "last_seen_ts": now,
            },
        ]
        original = incidents_module.issue_scopes
        calls = 0

        def counted_issue_scopes(issue):
            nonlocal calls
            calls += 1
            if calls > len(issues):
                self.fail("new incident groups should reuse precomputed scopes")
            return original(issue)

        incidents_module.issue_scopes = counted_issue_scopes
        try:
            groups = IncidentGrouper().group(issues)
        finally:
            incidents_module.issue_scopes = original

        self.assertEqual(len(groups), 2)
        self.assertEqual(calls, len(issues))

    def test_incident_grouper_prunes_expired_shared_scope_candidates(self):
        now = int(time.time() * 1000)
        issues = [
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "affected_locations": [f"survival/s{index}"],
                "first_seen_ts": now + index * 1000,
                "last_seen_ts": now + index * 1000,
            }
            for index in range(40)
        ]
        grouper = IncidentGrouper(merge_window_ms=1)
        original = grouper.can_merge_precomputed
        calls = 0

        def counted_can_merge(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 4:
                self.fail("expired shared server candidates should be pruned")
            return original(*args, **kwargs)

        grouper.can_merge_precomputed = counted_can_merge
        groups = grouper.group(issues)

        self.assertEqual(len(groups), len(issues))
        self.assertEqual(calls, 0)

    def test_player_problem_lines_reuses_prebuilt_incidents(self):
        now = int(time.time() * 1000)
        issue = {
            "category": "complaint",
            "tag": "performance_lag",
            "severity": "high",
            "players": ["Alice"],
            "affected_locations": ["survival/s1"],
            "first_seen_ts": now,
            "last_seen_ts": now,
            "suggested_action": "Check TPS.",
        }
        groups = IncidentGrouper().group([issue])
        original = text_renderer_module._INCIDENT_GROUPER.group

        def fail_if_regrouped(*args, **kwargs):
            self.fail("player problem rendering should reuse prebuilt incidents")

        text_renderer_module._INCIDENT_GROUPER.group = fail_if_regrouped
        try:
            lines = text_renderer_module._player_problem_lines(
                {"chat_players": []},
                [issue],
                groups,
            )
        finally:
            text_renderer_module._INCIDENT_GROUPER.group = original

        self.assertTrue(any("Alice" in line for line in lines))

    def test_text_action_lines_reuse_actionable_issues(self):
        original = text_renderer_module._ISSUE_POLICY.actionable_issues

        def fail_if_filtered_again(*args, **kwargs):
            self.fail("action lines should receive prefiltered actionable issues")

        text_renderer_module._ISSUE_POLICY.actionable_issues = fail_if_filtered_again
        try:
            lines = text_renderer_module._action_lines(
                [
                    {
                        "category": "complaint",
                        "tag": "performance_lag",
                        "severity": "high",
                        "players": ["Alice"],
                        "affected_locations": ["survival/s1"],
                        "suggested_action": "Check TPS.",
                    }
                ]
            )
        finally:
            text_renderer_module._ISSUE_POLICY.actionable_issues = original

        self.assertEqual(lines, ["运维排查（玩家 Alice；位置 survival/s1）：Check TPS."])

    def test_text_action_lines_group_admin_work_by_handling_type(self):
        lines = text_renderer_module._action_lines(
            [
                {
                    "category": "moderation",
                    "tag": "chat_conflict",
                    "severity": "high",
                    "players": ["Alice", "Bob"],
                    "suggested_action": "Review chat context.",
                },
                {
                    "category": "complaint",
                    "tag": "performance_lag",
                    "severity": "high",
                    "affected_locations": ["survival/s1"],
                    "suggested_action": "Check TPS and memory.",
                },
                {
                    "category": "economy",
                    "tag": "economy_or_shop_abuse",
                    "severity": "medium",
                    "players": ["Carol"],
                    "suggested_action": "Audit shop logs.",
                },
                {
                    "category": "suggestion",
                    "tag": "player_suggestion",
                    "severity": "low",
                    "players": ["Dave"],
                    "suggested_action": "Collect as feedback.",
                },
            ]
        )

        self.assertEqual(
            lines,
            [
                "社群处理（玩家 Alice、Bob）：Review chat context.",
                "运维排查（位置 survival/s1）：Check TPS and memory.",
                "数据核对（玩家 Carol）：Audit shop logs.",
                "体验反馈（玩家 Dave）：Collect as feedback.",
            ],
        )

    def test_text_incident_helpers_reuse_sorted_issues(self):
        issues = [
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "suggested_action": "Check TPS.",
            }
        ]
        original = text_renderer_module.issue_sort_key

        def fail_if_sorted_again(*args, **kwargs):
            self.fail("incident helpers should reuse supplied sorted issues")

        text_renderer_module.issue_sort_key = fail_if_sorted_again
        try:
            labels = text_renderer_module._incident_labels(issues, issues)
            action = text_renderer_module._incident_action(issues, issues)
        finally:
            text_renderer_module.issue_sort_key = original

        self.assertTrue(labels)
        self.assertTrue(action.startswith("Check TPS"))

    def test_image_incident_helpers_reuse_sorted_issues(self):
        issues = [
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "suggested_action": "Check TPS.",
                "evidence_samples": ["context\n> TPS dropped"],
            }
        ]
        renderer = SimpleNamespace(
            issue_title=image_renderer_module.DEFAULT_LABELS.issue_title
        )
        original = image_renderer_module.issue_sort_key

        def fail_if_sorted_again(*args, **kwargs):
            self.fail("image incident helpers should reuse supplied sorted issues")

        image_renderer_module.issue_sort_key = fail_if_sorted_again
        try:
            labels = image_renderer_module._incident_labels(renderer, issues, issues)
            actions = image_renderer_module._incident_action_lines(issues, 2, issues)
            evidence = image_renderer_module._incident_evidence_lines(issues, 2, issues)
        finally:
            image_renderer_module.issue_sort_key = original

        self.assertTrue(labels)
        self.assertEqual(actions, ["Check TPS."])
        self.assertEqual(evidence, ["TPS dropped"])

    def test_image_action_lines_group_admin_work_by_handling_type(self):
        lines = image_renderer_module._action_lines(
            [
                {
                    "category": "complaint",
                    "tag": "performance_lag",
                    "severity": "high",
                    "players": ["Alice"],
                    "affected_locations": ["survival/s1"],
                    "suggested_action": "Check TPS.",
                },
                {
                    "category": "moderation",
                    "tag": "chat_conflict",
                    "severity": "high",
                    "players": ["Bob"],
                    "suggested_action": "Review chat context.",
                },
            ]
        )

        self.assertEqual(
            lines,
            [
                "运维排查（玩家 Alice；位置 survival/s1）：Check TPS.",
                "社群处理（玩家 Bob）：Review chat context.",
            ],
        )

    def test_heuristic_report_skips_evidence_text_when_samples_disabled(self):
        now = int(time.time() * 1000)
        record = ObservationRecord(
            event_id="plugin-error",
            kind="PLUGIN_ERROR",
            timestamp=now,
            server_id="survival",
            content="plugin exception",
        )
        original = ObservationRecord.evidence_text

        def fail_if_called(_record):
            self.fail("evidence_text should be lazy when samples are disabled")

        ObservationRecord.evidence_text = fail_if_called
        try:
            report = HeuristicReportBuilder(
                MineSentinelConfig.from_dict(
                    {"report": {"include_evidence_samples": False}}
                )
            ).build([record], 30, "survival")
        finally:
            ObservationRecord.evidence_text = original

        issue = next(issue for issue in report["issues"] if issue["tag"] == "plugin/exception")
        self.assertEqual(issue["evidence_samples"], [])

    def test_heuristic_report_does_not_rebuild_empty_metric_context(self):
        now = int(time.time() * 1000)
        record = ObservationRecord(
            event_id="plugin-error",
            kind="PLUGIN_ERROR",
            timestamp=now,
            server_id="survival",
            content="plugin exception",
        )
        original = reporting_rules_module.build_metric_context

        def fail_if_full_pass_runs(records):
            self.fail("empty metric context should still be reused")

        reporting_rules_module.build_metric_context = fail_if_full_pass_runs
        try:
            report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
                [record],
                30,
                "survival",
            )
        finally:
            reporting_rules_module.build_metric_context = original

        self.assertTrue(report["ops_notes"])

    def test_report_merges_same_window_labels_into_incidents(self):
        now = int(time.time() * 1000)
        records = [
            self._chat_on_backend(
                now,
                "TestAlex",
                "s1",
                "我输入 /home 后卡在原地，随后被传送进虚空了。",
            ),
            self._chat_on_backend(
                now + 1000,
                "TestSteve",
                "s1",
                "今晚掉线三次了，连接稳定性是不是有问题？",
            ),
            self._chat_on_backend(
                now + 2000,
                "TestMia",
                "s1",
                "商店扣了金币却没有给我物品，管理员能查一下吗？",
            ),
            self._chat_on_backend(
                now + 3000,
                "TestLuna",
                "s1",
                "切换世界后血量和背包不同步，像 bug 一样。",
            ),
            self._chat_on_backend(
                now + 4000,
                "TestNoah",
                "s1",
                "我录到了疑似飞行的玩家，需要在哪里提交视频？",
            ),
            self._metric_on_backend(now + 5000, "s1", 20.0, 91.8),
        ]

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            360,
            "survival",
        )
        text = format_report(report, len(records), 0, 5)
        event_section = text.split("三、玩家问题/投诉识别", 1)[0].split(
            "二、聊天与事件总结",
            1,
        )[1]

        self.assertEqual(text.count("事件 #1"), 1)
        self.assertEqual(text.count("事件 #2"), 1)
        self.assertIn("服务器集中出现多类异常反馈", event_section)
        self.assertIn("疑似作弊/破坏或利用漏洞反馈", event_section)
        self.assertIn("卡顿/延迟反馈", event_section)
        self.assertIn("跨服/传送异常", event_section)
        self.assertIn("经济/商店异常", event_section)
        first_event = event_section.split("2. 事件 #2", 1)[0]
        first_players = re.search(r"相关玩家：([^。]+)。", first_event)
        self.assertIsNotNone(first_players)
        self.assertNotIn("TestNoah", first_players.group(1))
        self.assertIn("同窗口指标：survival/s1", event_section)
        self.assertNotIn("server_metrics", event_section)
        self.assertIn("有 2 个事故级问题需要优先确认", text)

    def test_alert_messages_include_player_names(self):
        config = MineSentinelConfig.from_dict(
            {
                "alert": {
                    "enabled": True,
                    "min_severity": "medium",
                    "min_evidence_count": 1,
                    "min_unique_players": 1,
                }
            }
        )
        alerts = MineSentinelAlertEngine(config)
        report = {
            "issues": [
                {
                    "category": "complaint",
                    "tag": "performance_lag",
                    "severity": "high",
                    "evidence_count": 2,
                    "signal_count": 1,
                    "unique_players": 2,
                    "incident_index": 1,
                    "first_seen_ts": 1700000000000,
                    "last_seen_ts": 1700000060000,
                    "players_text": "Alice、Bob",
                    "mentioned_players_text": "Carol",
                    "affected_locations_text": "survival/s1",
                    "dialogue_terms": ["卡", "延迟"],
                    "metric_context_text": "survival/s1 TPS最低 12.4，内存最高 91.2%",
                    "suggested_action": "检查 TPS。",
                    "should_alert": True,
                }
            ]
        }

        messages = alerts.build_messages("survival", report)

        self.assertEqual(len(messages), 1)
        self.assertIn("玩家：Alice、Bob", messages[0])
        self.assertIn("事件 #2", messages[0])
        self.assertIn("时间：", messages[0])
        self.assertIn("去重信号：1 个", messages[0])
        self.assertIn("提到玩家：Carol", messages[0])
        self.assertIn("位置：survival/s1", messages[0])
        self.assertIn("关键词：卡、延迟", messages[0])
        self.assertIn("指标：survival/s1 TPS最低 12.4", messages[0])

    def test_dialogue_negation_is_term_scoped(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        records = [
            self._chat(now - 2000, "Alice", "今天不卡"),
            self._chat(now - 1000, "Bob", "不卡但是一直掉线"),
        ]

        result = analyzer.analyze(records)

        issue_tags = {issue["tag"] for issue in result["issues"]}
        self.assertNotIn("performance_lag", issue_tags)
        self.assertIn("disconnect_or_rollback", issue_tags)

    def test_dialogue_counts_full_window_while_bounding_samples(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {
                    "dialogue": {"max_issue_records": 2},
                    "report": {"max_evidence_samples": 5},
                }
            )
        )
        records = [
            self._chat(now - 4000, "Alice", "服务器卡"),
            self._chat(now - 3000, "Alice", "还是卡"),
            self._chat(now - 2000, "Alice", "一直卡"),
            self._chat(now - 1000, "Bob", "我也卡到玩不了"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_count"], 4)
        self.assertEqual(issue["signal_count"], 4)
        self.assertEqual(issue["unique_players"], 2)
        self.assertEqual(issue["players"], ["Alice", "Bob"])
        self.assertLessEqual(len(issue["evidence_samples"]), 2)
        self.assertTrue(any("Bob" in sample for sample in issue["evidence_samples"]))

    def test_dialogue_evidence_samples_include_same_location_context(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {
                    "dialogue": {
                        "context_window_seconds": 120,
                        "context_messages_per_side": 1,
                    }
                }
            )
        )
        records = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "刚进服有点慢"),
            self._chat_on_backend(now - 2000, "Bob", "s2", "我在别的服聊天"),
            self._chat_on_backend(now - 1000, "Carol", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now, "Dave", "s1", "我也是"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        sample = issue["evidence_samples"][0]
        self.assertIn("上下文 survival/s1", sample)
        self.assertIn("Alice: 刚进服有点慢", sample)
        self.assertIn("> ", sample)
        self.assertIn("Carol: 服务器卡到玩不了", sample)
        self.assertIn("Dave: 我也是", sample)
        self.assertNotIn("Bob: 我在别的服聊天", sample)

    def test_dialogue_context_builds_only_output_evidence_samples(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {
                    "dialogue": {
                        "context_window_seconds": 120,
                        "context_messages_per_side": 1,
                        "max_issue_records": 10,
                    },
                    "report": {"max_evidence_samples": 2},
                }
            )
        )
        records = [
            self._chat_on_backend(
                now + index * 1000,
                f"Player{index}",
                "s1",
                "server lag is bad",
            )
            for index in range(8)
        ]
        original = dialogue_evidence_module.DialogueEvidenceContextBuilder._context_sample
        calls = 0

        def counted_context_sample(builder, record, buckets, indexes):
            nonlocal calls
            calls += 1
            if calls > 2:
                self.fail("dialogue context should only be synthesized for output samples")
            return original(builder, record, buckets, indexes)

        dialogue_evidence_module.DialogueEvidenceContextBuilder._context_sample = (
            counted_context_sample
        )
        try:
            result = analyzer.analyze(records)
        finally:
            dialogue_evidence_module.DialogueEvidenceContextBuilder._context_sample = original

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_count"], len(records))
        self.assertEqual(len(issue["evidence_samples"]), 2)
        self.assertEqual(calls, 2)

    def test_dialogue_context_skips_work_when_evidence_samples_disabled(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {
                    "dialogue": {
                        "context_window_seconds": 120,
                        "context_messages_per_side": 1,
                    },
                    "report": {"include_evidence_samples": False},
                }
            )
        )
        records = [
            self._chat_on_backend(now, "Alice", "s1", "server lag is bad")
        ]
        original = dialogue_evidence_module.DialogueEvidenceContextBuilder.attach

        def fail_if_attached(*args, **kwargs):
            self.fail("dialogue context should be skipped when samples are disabled")

        dialogue_evidence_module.DialogueEvidenceContextBuilder.attach = fail_if_attached
        try:
            result = analyzer.analyze(records)
        finally:
            dialogue_evidence_module.DialogueEvidenceContextBuilder.attach = original

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_samples"], [])

    def test_dialogue_evidence_context_can_be_disabled(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {"dialogue": {"context_window_seconds": 0}}
            )
        )
        records = [
            self._chat_on_backend(now - 1000, "Alice", "s1", "刚进服有点慢"),
            self._chat_on_backend(now, "Bob", "s1", "服务器卡到玩不了"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        sample = issue["evidence_samples"][0]
        self.assertNotIn("上下文", sample)
        self.assertIn("Bob: 服务器卡到玩不了", sample)

    def test_dialogue_signal_collector_tracks_deduped_locations_and_samples(self):
        now = int(time.time() * 1000)
        rule = next(rule for rule in DIALOGUE_RULES if rule.tag == "performance_lag")
        collector = DialogueSignalCollector(max_issue_records=2)
        records = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now - 2000, "Alice", "s1", "服务器 卡到 玩不了!!!"),
            self._chat_on_backend(now - 1000, "Bob", "s2", "我也卡，延迟很高"),
        ]
        for record in records:
            text = normalize_text(record.content)
            collector.add(record, rule, matched_terms(text, rule.keywords), text)

        group = collector.groups()[0]

        self.assertEqual(group.evidence_count, 3)
        self.assertEqual(group.signal_count, 2)
        self.assertEqual(group.distinct_message_count, 2)
        self.assertEqual(group.players, {"Alice", "Bob"})
        self.assertEqual(group.locations, {"survival/s1", "survival/s2"})
        self.assertLessEqual(len(group.records), 2)
        self.assertTrue(any(record.player_name == "Bob" for record in group.records))

    def test_dialogue_signal_collector_splits_incidents_by_time_gap(self):
        now = int(time.time() * 1000)
        rule = next(rule for rule in DIALOGUE_RULES if rule.tag == "performance_lag")
        collector = DialogueSignalCollector(
            max_issue_records=2,
            incident_gap_seconds=60,
        )
        early = self._chat_on_backend(now - 180000, "Alice", "s1", "服务器卡")
        late = self._chat_on_backend(now, "Bob", "s1", "服务器又卡了")
        for record in (early, late):
            text = normalize_text(record.content)
            collector.add(record, rule, matched_terms(text, rule.keywords), text)

        groups = collector.groups()

        self.assertEqual(len(groups), 2)
        self.assertEqual([group.incident_index for group in groups], [0, 1])
        self.assertEqual([group.players for group in groups], [{"Alice"}, {"Bob"}])

    def test_dialogue_issue_builder_formats_report_sections(self):
        now = int(time.time() * 1000)
        config = MineSentinelConfig.from_dict(
            {
                "alert": {
                    "enabled": True,
                    "min_severity": "high",
                    "min_evidence_count": 2,
                    "min_unique_players": 2,
                }
            }
        )
        rule = next(rule for rule in DIALOGUE_RULES if rule.tag == "performance_lag")
        collector = DialogueSignalCollector(max_issue_records=3)
        records = [
            self._chat_on_backend(now - 2000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now - 1000, "Bob", "s2", "我也卡，延迟很高"),
        ]
        for record in records:
            text = normalize_text(record.content)
            collector.add(record, rule, matched_terms(text, rule.keywords), text)

        result = DialogueIssueBuilder(config).build(collector.groups())
        issue = result["issues"][0]

        self.assertEqual(issue["source_tag"], "dialogue:performance_lag")
        self.assertEqual(issue["affected_locations"], ["survival/s1", "survival/s2"])
        self.assertEqual(issue["players"], ["Alice", "Bob"])
        self.assertEqual(len(issue["evidence_samples"]), 2)
        self.assertTrue(issue["should_alert"])
        self.assertIn("卡顿/延迟反馈", result["findings"][0])
        self.assertIn("complaint", result["category_lines"])

    def test_dialogue_issue_builder_top_k_keeps_stable_tie_order(self):
        now = int(time.time() * 1000)
        config = MineSentinelConfig.from_dict(
            {
                "dialogue": {
                    "max_findings": 2,
                    "min_evidence_count": 1,
                    "min_issue_score": 0,
                }
            }
        )
        rule = next(rule for rule in DIALOGUE_RULES if rule.tag == "performance_lag")
        collector = DialogueSignalCollector(
            max_issue_records=1,
            incident_gap_seconds=1,
        )
        for offset, player in enumerate(("Alice", "Bob", "Carol")):
            record = self._chat_on_backend(
                now + offset * 5000,
                player,
                "s1",
                "lag",
            )
            text = normalize_text(record.content)
            collector.add(record, rule, matched_terms(text, rule.keywords), text)

        result = DialogueIssueBuilder(config).build(collector.groups())

        self.assertEqual(
            [issue["players"] for issue in result["issues"]],
            [["Alice"], ["Bob"]],
        )

    def test_dialogue_incident_gap_splits_same_rule_report_issues(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {"dialogue": {"incident_gap_seconds": 60}}
            )
        )
        records = [
            self._chat_on_backend(now - 180000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now - 179000, "Bob", "s1", "我也卡"),
            self._chat_on_backend(now, "Carol", "s1", "服务器又卡了"),
            self._chat_on_backend(now + 1000, "Dave", "s1", "我也卡"),
        ]

        result = analyzer.analyze(records)

        issues = [
            issue for issue in result["issues"] if issue["tag"] == "performance_lag"
        ]
        self.assertEqual(len(issues), 2)
        self.assertEqual([issue["incident_index"] for issue in issues], [0, 1])
        self.assertEqual(issues[0]["players"], ["Alice", "Bob"])
        self.assertEqual(issues[1]["players"], ["Carol", "Dave"])

    def test_dialogue_incident_gap_can_be_disabled(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {"dialogue": {"incident_gap_seconds": 0}}
            )
        )
        records = [
            self._chat_on_backend(now - 180000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now, "Bob", "s1", "服务器又卡了"),
        ]

        result = analyzer.analyze(records)

        issues = [
            issue for issue in result["issues"] if issue["tag"] == "performance_lag"
        ]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["players"], ["Alice", "Bob"])

    def test_dialogue_score_and_severity_are_scope_aware(self):
        rule = next(rule for rule in DIALOGUE_RULES if rule.tag == "performance_lag")

        single_location_score = dialogue_score(
            signal_count=2,
            unique_player_count=2,
            urgent_signal_count=0,
            affected_location_count=1,
            affected_server_count=1,
        )
        wide_location_score = dialogue_score(
            signal_count=2,
            unique_player_count=2,
            urgent_signal_count=0,
            affected_location_count=2,
            affected_server_count=1,
        )

        self.assertGreater(wide_location_score, single_location_score)
        self.assertEqual(
            dialogue_severity(
                rule,
                signal_count=2,
                unique_player_count=2,
                urgent_signal_count=0,
                affected_location_count=2,
                affected_server_count=1,
            ),
            "critical",
        )

    def test_dialogue_issue_includes_backend_distribution(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        records = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "服务器卡到动不了"),
            self._chat_on_backend(now - 2000, "Bob", "s2", "我也卡，延迟很高"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["affected_backends"], ["s1", "s2"])
        self.assertEqual(issue["affected_locations"], ["survival/s1", "survival/s2"])
        self.assertEqual(
            issue["affected_locations_text"],
            "survival/s1、survival/s2",
        )
        self.assertTrue(any("位置 survival/s1、survival/s2" in item for item in result["findings"]))

    def test_metric_context_aggregates_by_backend_location(self):
        now = int(time.time() * 1000)
        records = [
            self._metric_on_backend(now - 2000, "s1", 12.45, 0.91),
            self._metric_on_backend(now - 1000, "s1", 19.8, 0.72),
            self._metric_on_backend(now, "s2", 20.0, 0.51),
        ]

        metrics = build_metric_context(records)

        self.assertEqual(metrics["survival/s1"]["samples"], 2)
        self.assertEqual(metrics["survival/s1"]["min_tps"], 12.45)
        self.assertEqual(metrics["survival/s1"]["max_memory_percent"], 91.0)
        self.assertEqual(metrics["survival/s1"]["low_tps_count"], 1)
        self.assertEqual(metrics["survival/s1"]["high_memory_count"], 1)
        self.assertEqual(metrics["survival/s2"]["low_tps_count"], 0)

    def test_metric_context_accepts_java_memory_mb_pair(self):
        now = int(time.time() * 1000)
        records = [
            ObservationRecord(
                event_id="metric-java",
                kind="SERVER_METRICS",
                timestamp=now,
                server_id="survival",
                backend_server="s1",
                metrics={
                    "tps1m": 19.5,
                    "memoryUsedMb": 3900,
                    "memoryMaxMb": 4096,
                },
            )
        ]

        metrics = build_metric_context(records)

        self.assertEqual(metrics["survival/s1"]["memory_samples"], 1)
        self.assertEqual(metrics["survival/s1"]["max_memory_percent"], 95.21)
        self.assertEqual(metrics["survival/s1"]["high_memory_count"], 1)

    def test_performance_dialogue_issue_includes_metric_context(self):
        now = int(time.time() * 1000)
        records = [
            self._chat_on_backend(now - 4000, "Alice", "s1", "服务器卡到动不了"),
            self._chat_on_backend(now - 3000, "Bob", "s1", "我也 lag，一直延迟"),
            self._metric_on_backend(now - 2000, "s1", 12.4, 91.2),
            self._metric_on_backend(now - 1000, "s2", 20.0, 48.0),
        ]

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            480,
            "survival",
        )

        issue = next(
            item for item in report["issues"] if item["tag"] == "performance_lag"
        )
        self.assertIn("survival/s1", issue["metric_context_text"])
        self.assertIn("TPS最低 12.4", issue["metric_context_text"])
        self.assertIn("内存最高 91.2%", issue["metric_context_text"])
        self.assertNotIn("survival/s2", issue["metric_context_text"])
        self.assertTrue(issue["metric_context"]["has_low_tps"])
        self.assertTrue(issue["metric_context"]["has_high_memory"])

        text = format_report(report, len(records), 0, 2)

        self.assertIn("时间范围：", text)
        self.assertIn("同窗口指标：survival/s1", text)
        self.assertIn("TPS最低 12.4", text)
        self.assertIn("上下文", text)
        self.assertIn("Alice: 服务器卡到动不了", text)

    def test_ai_normalization_preserves_metric_context(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.reporting.ai_summary import (
                AIReportSummarizer,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.reporting.ai_summary import AIReportSummarizer

        summarizer = AIReportSummarizer(MineSentinelConfig.from_dict({}))
        fallback = self._fallback_report(
            {
                "category": "complaint",
                "tag": "performance_lag",
                "severity": "high",
                "players": ["Alice", "Bob"],
                "players_text": "Alice、Bob",
                "affected_locations": ["survival/s1"],
                "affected_locations_text": "survival/s1",
                "metric_context_text": "survival/s1 TPS最低 12.4，内存最高 91.2%",
                "metric_context": {
                    "locations_text": "survival/s1",
                    "has_low_tps": True,
                    "has_high_memory": True,
                    "text": "survival/s1 TPS最低 12.4，内存最高 91.2%",
                },
                "evidence_count": 2,
                "signal_count": 2,
                "unique_players": 2,
                "suggested_action": "检查 TPS。",
            }
        )
        data = {
            "issues": [
                {
                    "category": "complaint",
                    "tag": "performance_lag",
                    "severity": "high",
                    "players": ["Alice", "Bob"],
                    "suggested_action": "检查服务端指标。",
                }
            ]
        }

        report = summarizer.normalizer.normalize_report(data, fallback)
        issue = report["issues"][0]

        self.assertEqual(
            issue["metric_context_text"],
            "survival/s1 TPS最低 12.4，内存最高 91.2%",
        )
        self.assertTrue(issue["metric_context"]["has_low_tps"])

    def test_ai_normalization_restores_admin_action_fields(self):
        fallback = self._fallback_report(
            {
                "category": "bug",
                "tag": "data_converter_error",
                "severity": "high",
                "players": ["Admin"],
                "affected_locations": ["spawn"],
                "evidence_samples": [
                    "[spawn] Failed to convert json to nbt: Small Flower Streamer"
                ],
                "evidence_count": 3,
                "signal_count": 3,
                "unique_players": 1,
                "suggested_action": "请定位触发 JSON/NBT 转换失败的资源或家具配置。",
                "should_alert": True,
            }
        )
        data = {
            "issues": [
                {
                    "category": "bug",
                    "tag": "data_converter_error",
                    "severity": "not-sure",
                    "players": ["Admin"],
                    "evidence_samples": [],
                }
            ]
        }

        report = AIReportNormalizer().normalize_report(data, fallback)
        issue = report["issues"][0]

        self.assertEqual(issue["severity"], "high")
        self.assertEqual(
            issue["suggested_action"],
            "请定位触发 JSON/NBT 转换失败的资源或家具配置。",
        )
        self.assertTrue(issue["should_alert"])
        self.assertEqual(
            issue["evidence_samples"],
            ["[spawn] Failed to convert json to nbt: Small Flower Streamer"],
        )

    def test_ai_normalization_preserves_same_tag_incident_identity(self):
        fallback = self._fallback_report(
            {
                "category": "complaint",
                "tag": "performance_lag",
                "source_tag": "dialogue:performance_lag",
                "incident_index": 0,
                "severity": "high",
                "players": ["Alice"],
                "dialogue_terms": ["卡"],
                "evidence_samples": ["上下文 survival/s1:\n> Alice: 服务器卡"],
                "evidence_count": 2,
                "signal_count": 2,
                "unique_players": 1,
            }
        )
        fallback["issues"].append(
            {
                "category": "complaint",
                "tag": "performance_lag",
                "source_tag": "dialogue:performance_lag",
                "incident_index": 1,
                "severity": "high",
                "players": ["Carol"],
                "dialogue_terms": ["延迟"],
                "evidence_samples": ["上下文 survival/s1:\n> Carol: 服务器延迟"],
                "evidence_count": 3,
                "signal_count": 3,
                "unique_players": 1,
            }
        )
        data = {
            "issues": [
                {"category": "complaint", "tag": "performance_lag"},
                {
                    "category": "complaint",
                    "tag": "performance_lag",
                    "incident_index": "1",
                },
            ]
        }

        report = AIReportNormalizer().normalize_report(data, fallback)

        self.assertEqual(
            [issue["incident_index"] for issue in report["issues"]],
            [0, 1],
        )
        self.assertEqual(
            [issue["players"] for issue in report["issues"]],
            [["Alice"], ["Carol"]],
        )
        self.assertEqual(
            [issue["dialogue_terms"] for issue in report["issues"]],
            [["卡"], ["延迟"]],
        )
        self.assertEqual(
            [issue["evidence_samples"][0] for issue in report["issues"]],
            [
                "上下文 survival/s1:\n> Alice: 服务器卡",
                "上下文 survival/s1:\n> Carol: 服务器延迟",
            ],
        )

    def test_formatter_translates_merged_raw_tags_to_chinese(self):
        now = int(time.time() * 1000)
        report = self._fallback_report(
            {
                "category": "complaint",
                "tag": "performance_lag,disconnect_or_rollback,cross_server_transfer",
                "title": "performance_lag,disconnect_or_rollback,cross_server_transfer",
                "severity": "high",
                "players": ["Alice", "Bob"],
                "players_text": "Alice、Bob",
                "affected_locations": ["survival/s1"],
                "affected_locations_text": "survival/s1",
                "first_seen_ts": now,
                "last_seen_ts": now,
                "evidence_count": 2,
                "signal_count": 2,
                "unique_players": 2,
                "evidence_samples": [
                    "上下文 survival/s1:\n> Alice: 服务器卡\n> Bob: 传送失败"
                ],
                "suggested_action": "人工复核。",
            }
        )

        text = format_report(report, 2, 0, 2)

        self.assertIn("卡顿/延迟反馈、掉线/回档反馈、跨服/传送异常", text)
        self.assertNotIn("performance_lag", text)
        self.assertNotIn("disconnect_or_rollback", text)
        self.assertNotIn("cross_server_transfer", text)

    def test_ai_normalization_drops_unmatched_merged_issue(self):
        fallback = self._fallback_report(
            {
                "category": "complaint",
                "tag": "performance_lag",
                "source_tag": "dialogue:performance_lag",
                "incident_index": 0,
                "severity": "high",
                "players": ["Alice"],
                "players_text": "Alice",
                "evidence_count": 1,
                "signal_count": 1,
                "unique_players": 1,
            }
        )
        fallback["issues"].append(
            {
                "category": "economy",
                "tag": "economy_or_shop_abuse",
                "source_tag": "dialogue:economy_or_shop_abuse",
                "incident_index": 0,
                "severity": "high",
                "players": ["Bob"],
                "players_text": "Bob",
                "evidence_count": 1,
                "signal_count": 1,
                "unique_players": 1,
            }
        )
        data = {
            "issues": [
                {
                    "category": "complaint",
                    "tag": "performance_lag,economy_or_shop_abuse",
                    "severity": "high",
                    "players": ["Alice", "Bob"],
                    "suggested_action": "检查所有问题。",
                }
            ]
        }

        report = AIReportNormalizer().normalize_report(data, fallback)

        self.assertEqual(
            [issue["tag"] for issue in report["issues"]],
            ["performance_lag", "economy_or_shop_abuse"],
        )

    def test_dialogue_wide_backend_scope_increases_severity_and_score(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        single_backend = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "服务器卡到动不了"),
            self._chat_on_backend(now - 2000, "Bob", "s1", "我也卡，延迟很高"),
        ]
        multi_backend = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "服务器卡到动不了"),
            self._chat_on_backend(now - 2000, "Bob", "s2", "我也卡，延迟很高"),
        ]

        single_issue = next(
            item
            for item in analyzer.analyze(single_backend)["issues"]
            if item["tag"] == "performance_lag"
        )
        multi_issue = next(
            item
            for item in analyzer.analyze(multi_backend)["issues"]
            if item["tag"] == "performance_lag"
        )

        self.assertEqual(single_issue["severity"], "high")
        self.assertEqual(multi_issue["severity"], "critical")
        self.assertGreater(multi_issue["score"], single_issue["score"])

    def test_dialogue_spam_does_not_inflate_signal_count_or_alert(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {
                    "alert": {
                        "enabled": True,
                        "min_severity": "high",
                        "min_evidence_count": 3,
                        "min_unique_players": 1,
                    }
                }
            )
        )
        records = [
            self._chat(now - idx * 1000, "Alice", "服务器卡到玩不了")
            for idx in range(10)
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_count"], 10)
        self.assertEqual(issue["signal_count"], 1)
        self.assertEqual(issue["distinct_message_count"], 1)
        self.assertEqual(issue["severity"], "high")
        self.assertFalse(issue["should_alert"])
        self.assertTrue(any("1 个去重信号" in item for item in result["findings"]))

    def test_dialogue_near_duplicate_spam_is_one_signal(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        records = [
            self._chat(now - 3000, "Alice", "服务器卡到玩不了!!!"),
            self._chat(now - 2000, "Alice", "服务器 卡到 玩不了..."),
            self._chat(now - 1000, "Alice", "服务器卡到玩不了？？？"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_count"], 3)
        self.assertEqual(issue["signal_count"], 1)
        self.assertEqual(issue["distinct_message_count"], 1)

    def test_dialogue_infers_short_followup_from_recent_same_location_issue(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        records = [
            self._chat_on_backend(now - 3000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now - 2000, "Bob", "s1", "我也是"),
            self._chat_on_backend(now - 1000, "Carol", "s1", "+1 一样"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_count"], 3)
        self.assertEqual(issue["signal_count"], 3)
        self.assertEqual(issue["players"], ["Alice", "Bob", "Carol"])
        self.assertIn("跟进反馈", issue["dialogue_terms"])
        self.assertTrue(is_continuation_message("+1 一样"))

    def test_dialogue_followup_does_not_cross_backend_or_expired_window(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(
            MineSentinelConfig.from_dict(
                {"dialogue": {"continuation_window_seconds": 30}}
            )
        )
        records = [
            self._chat_on_backend(now - 120000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now - 1000, "Bob", "s1", "我也是"),
            self._chat_on_backend(now, "Carol", "s2", "我也是"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(issue["evidence_count"], 1)
        self.assertEqual(issue["players"], ["Alice"])

    def test_dialogue_followup_can_be_disabled(self):
        now = int(time.time() * 1000)
        config = MineSentinelConfig.from_dict(
            {"dialogue": {"continuation_window_seconds": 0}}
        )
        analyzer = PlayerDialogueAnalyzer(config)
        records = [
            self._chat_on_backend(now - 1000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now, "Bob", "s1", "我也是"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "performance_lag"
        )
        self.assertEqual(config.dialogue.continuation_window_seconds, 0)
        self.assertEqual(issue["players"], ["Alice"])

    def test_dialogue_issue_suppresses_duplicate_generic_issue(self):
        now = int(time.time() * 1000)
        records = [
            self._chat_on_backend(now - 2000, "Alice", "s1", "服务器卡到玩不了"),
            self._chat_on_backend(now - 1000, "Bob", "s1", "我也卡"),
        ]

        report = HeuristicReportBuilder(MineSentinelConfig.from_dict({})).build(
            records,
            480,
            "survival",
        )

        performance_issues = [
            issue
            for issue in report["issues"]
            if issue.get("source_tag") == "dialogue:performance_lag"
        ]
        self.assertEqual(len(performance_issues), 1)
        self.assertEqual(
            [issue for issue in report["issues"] if issue["category"] == "complaint"],
            performance_issues,
        )

    def test_dialogue_extracts_mentioned_players(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        records = [
            self._chat(now - 2000, "Alice", "Bob 开挂，还疑似透视"),
            self._chat(now - 1000, "Carol", "@Bob 作弊太明显了"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "cheat_or_grief_report"
        )
        self.assertEqual(issue["players"], ["Alice", "Carol"])
        self.assertEqual(issue["mentioned_players"], ["Bob"])
        self.assertTrue(any("提到 Bob" in item for item in result["findings"]))

    def test_dialogue_abuse_terms_take_moderation_priority(self):
        now = int(time.time() * 1000)
        analyzer = PlayerDialogueAnalyzer(MineSentinelConfig.from_dict({}))
        records = [
            self._chat(now, "Noah", "有人利用 bug 复制物品，这应该立刻处理吗？")
        ]

        result = analyzer.analyze(records)
        tags = {item["tag"] for item in result["issues"]}

        self.assertIn("cheat_or_grief_report", tags)
        self.assertNotIn("economy_or_shop_abuse", tags)
        self.assertNotIn("feature_broken", tags)

    def test_dialogue_custom_rule_detects_server_specific_issue(self):
        now = int(time.time() * 1000)
        config = MineSentinelConfig.from_dict(
            {
                "dialogue": {
                    "custom_rules": [
                        {
                            "category": "bug",
                            "tag": "quest_npc_broken",
                            "title": "任务 NPC 异常",
                            "keywords": ["npc不见了", "任务交不了"],
                            "urgent_terms": ["所有人"],
                            "suggested_action": "检查任务插件和 NPC 刷新日志。",
                            "base_severity": "high",
                        }
                    ]
                }
            }
        )
        analyzer = PlayerDialogueAnalyzer(config)
        records = [
            self._chat(now - 2000, "Alice", "主城npc不见了"),
            self._chat(now - 1000, "Bob", "我任务交不了"),
        ]

        result = analyzer.analyze(records)

        issue = next(
            item for item in result["issues"] if item["tag"] == "custom_quest_npc_broken"
        )
        self.assertEqual(issue["category"], "bug")
        self.assertEqual(issue["players"], ["Alice", "Bob"])
        self.assertEqual(issue["suggested_action"], "检查任务插件和 NPC 刷新日志。")
        self.assertIn("任务 NPC 异常", result["findings"][0])

    def test_custom_dialogue_rules_are_sanitized_and_require_keywords(self):
        rules = custom_dialogue_rules(
            [
                {
                    "category": "unknown",
                    "tag": "坏 标签!",
                    "keywords": ["自定义坏了"],
                    "base_severity": "loud",
                },
                {
                    "category": "bug",
                    "tag": "empty",
                    "keywords": [],
                },
            ]
        )

        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].category, "complaint")
        self.assertEqual(rules[0].tag, "custom_rule_0")
        self.assertEqual(rules[0].base_severity, "medium")

    def test_custom_dialogue_rule_tags_are_namespaced_and_deduped(self):
        rules = custom_dialogue_rules(
            [
                {"tag": "performance_lag", "keywords": ["专属卡顿词"]},
                {"tag": "npc", "keywords": ["npc不见了"]},
                {"tag": "npc", "keywords": ["npc卡住了"]},
            ]
        )

        self.assertEqual(
            [rule.tag for rule in rules],
            ["custom_performance_lag", "custom_npc", "custom_npc_2"],
        )

    @staticmethod
    def _chat(timestamp: int, player: str, content: str) -> ObservationRecord:
        return ObservationRecord(
            event_id=f"{player}-{timestamp}",
            kind="CHAT",
            timestamp=timestamp,
            server_id="survival",
            backend_server="s1",
            player_name=player,
            player_uuid_hash=f"uuid-{player}",
            content=content,
        )

    @staticmethod
    def _chat_on_backend(
        timestamp: int,
        player: str,
        backend: str,
        content: str,
    ) -> ObservationRecord:
        return ObservationRecord(
            event_id=f"{player}-{backend}-{timestamp}",
            kind="CHAT",
            timestamp=timestamp,
            server_id="survival",
            backend_server=backend,
            player_name=player,
            player_uuid_hash=f"uuid-{player}",
            content=content,
        )

    @staticmethod
    def _metric(timestamp: int, tps: str) -> ObservationRecord:
        return ObservationRecord(
            event_id=f"metric-{timestamp}",
            kind="SERVER_METRICS",
            timestamp=timestamp,
            server_id="survival",
            backend_server="s1",
            metrics={"tps1m": tps},
        )

    @staticmethod
    def _metric_on_backend(
        timestamp: int,
        backend: str,
        tps: float,
        memory_percent: float,
    ) -> ObservationRecord:
        return ObservationRecord(
            event_id=f"metric-{backend}-{timestamp}",
            kind="SERVER_METRICS",
            timestamp=timestamp,
            server_id="survival",
            backend_server=backend,
            metrics={
                "tps1m": tps,
                "memoryUsagePercent": memory_percent,
            },
        )

    @staticmethod
    def _fallback_report(issue: dict) -> dict:
        return {
            "summary": "",
            "time_window": "最近 480 分钟",
            "servers": ["survival"],
            "chat_count": 0,
            "chat_players": [],
            "chat_players_text": "未知",
            "dialogue_findings": [],
            "categories": {
                "daily": [],
                "complaint": [],
                "bug": [],
                "economy": [],
                "moderation": [],
                "suggestion": [],
                "cross_server": [],
            },
            "issues": [issue],
            "ops_notes": [],
        }


class MineSentinelStorageTests(unittest.TestCase):
    def test_record_codec_bounds_content_metrics_and_raw(self):
        config = MineSentinelConfig.from_dict(
            {
                "max_tags_per_record": 1,
                "max_metric_fields": 1,
                "storage": {
                    "include_raw": False,
                    "max_content_length": 5,
                },
            }
        )
        codec = ObservationRecordCodec(config)
        record = ObservationRecord(
            event_id="evt-codec",
            kind="CHAT",
            timestamp=1,
            server_id="survival",
            player_name="Alice",
            content="abcdefg",
            tags=["longtag", "second"],
            metrics={"first": {"nested": "abcdefg"}, "second": 2},
            raw={"secret": "value"},
        )

        codec.normalize_record(record)
        payload = codec.record_to_json(record)

        self.assertEqual(record.content, "ab...")
        self.assertEqual(record.tags, ["lo..."])
        self.assertEqual(list(record.metrics), ["first"])
        self.assertEqual(record.context, {})
        self.assertEqual(record.raw, {})
        self.assertEqual(payload["raw"], {})

    def test_record_codec_compacts_nested_values_without_full_json_dump(self):
        codec = ObservationRecordCodec(
            MineSentinelConfig.from_dict(
                {
                    "max_raw_fields": 2,
                    "storage": {"max_content_length": 80},
                }
            )
        )
        huge_value = {
            "items": list(range(10000)),
            "details": {"nested": ["x" * 1000 for _ in range(1000)]},
            "extra": "trimmed",
        }
        original = storage_codec_module.json.dumps

        def guarded_dumps(value, *args, **kwargs):
            if value is huge_value:
                self.fail("compact_value should dump a bounded preview, not the full value")
            return original(value, *args, **kwargs)

        storage_codec_module.json.dumps = guarded_dumps
        try:
            compact = codec.compact_value(huge_value)
        finally:
            storage_codec_module.json.dumps = original

        self.assertLessEqual(len(compact), 80)
        self.assertIn("items", compact)
        self.assertIn("more", compact)

    def test_record_codec_preserves_mc_context_for_location(self):
        config = MineSentinelConfig.from_dict(
            {
                "max_raw_fields": 3,
                "storage": {
                    "include_raw": False,
                },
            }
        )
        codec = ObservationRecordCodec(config)
        record = ObservationRecord.from_dict(
            {
                "eventId": "evt-context",
                "kind": "CHAT",
                "timestamp": 1,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "刚才这里很卡",
                "context": {
                    "source": "bukkit",
                    "messageType": "PUBLIC_CHAT",
                    "world": "world_nether",
                    "extra": "trimmed",
                },
                "raw": {"secret": "value"},
            }
        )

        codec.normalize_record(record)
        payload = codec.record_to_json(record)

        self.assertEqual(record_location(record), "survival/s1@world_nether")
        self.assertEqual(
            payload["context"],
            {
                "source": "bukkit",
                "messageType": "PUBLIC_CHAT",
                "world": "world_nether",
            },
        )
        self.assertEqual(payload["raw"], {})

    def test_record_codec_reuses_chat_fingerprint_for_bucket_keys(self):
        codec = ObservationRecordCodec(MineSentinelConfig.from_dict({}))
        calls = 0
        original = storage_codec_module.message_fingerprint

        def counted_fingerprint(text):
            nonlocal calls
            calls += 1
            return original(text)

        storage_codec_module.message_fingerprint = counted_fingerprint
        try:
            keys = codec.raw_dedupe_keys(
                {
                    "kind": "CHAT",
                    "timestamp": 1001,
                    "serverId": "survival",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "lag!!!",
                },
                "survival",
            )
        finally:
            storage_codec_module.message_fingerprint = original

        self.assertEqual(len(keys), 2)
        self.assertEqual(calls, 1)

    def test_record_codec_event_non_chat_dedupe_skips_payload_work(self):
        codec = ObservationRecordCodec(MineSentinelConfig.from_dict({}))
        original = codec._raw_int

        def fail_if_timestamp_parsed(*args, **kwargs):
            self.fail("non-chat event dedupe should only use the event id")

        codec._raw_int = fail_if_timestamp_parsed
        try:
            keys = codec.raw_dedupe_keys(
                {
                    "eventId": "metric-event",
                    "kind": "SERVER_METRICS",
                    "timestamp": "not-needed",
                    "metrics": {"tps1m": 20.0},
                },
                "survival",
            )
        finally:
            codec._raw_int = original

        self.assertEqual(keys, ("event|metric-event",))

    def test_record_codec_payload_dedupe_hashes_fields_without_full_payload_dump(self):
        codec = ObservationRecordCodec(MineSentinelConfig.from_dict({}))
        payload_fields = {
            "backendServer",
            "content",
            "context",
            "metrics",
            "proxyId",
            "raw",
            "tags",
        }
        original = storage_codec_module.json.dumps

        def guarded_dumps(value, *args, **kwargs):
            if isinstance(value, dict) and set(value) == payload_fields:
                self.fail("payload dedupe should stream fields instead of dumping payload dict")
            return original(value, *args, **kwargs)

        storage_codec_module.json.dumps = guarded_dumps
        try:
            keys = codec.raw_dedupe_keys(
                {
                    "kind": "SERVER_METRICS",
                    "timestamp": 1000,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "metrics": {"memory": 0.5, "tps1m": 20.0},
                    "context": {"world": "world"},
                },
                "survival",
            )
        finally:
            storage_codec_module.json.dumps = original

        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].startswith("payload|SERVER_METRICS|survival||1000|"))

    def test_recent_window_builder_keeps_issue_signal_while_counting_full_window(self):
        now = int(time.time() * 1000)
        builder = RecentWindowBuilder(max_records=2)
        for idx in range(10):
            builder.add(
                ObservationRecord(
                    event_id=f"neutral-{idx}",
                    kind="CHAT",
                    timestamp=now - 10000 + idx,
                    server_id="survival",
                    backend_server="s1",
                    player_name=f"Neutral{idx}",
                    player_uuid_hash=f"n-{idx}",
                    content="今天继续建房子",
                )
            )
        builder.add(
            ObservationRecord(
                event_id="issue",
                kind="CHAT",
                timestamp=now,
                server_id="survival",
                backend_server="s1",
                player_name="Alice",
                player_uuid_hash="uuid-Alice",
                content="我的装备没了，能恢复吗",
            )
        )

        window = builder.build()

        self.assertEqual(window.total_count, 11)
        self.assertEqual(window.unique_players, 11)
        self.assertTrue(window.truncated)
        self.assertTrue(any(record.player_name == "Alice" for record in window.records))

    def test_recent_window_builder_prioritizes_custom_dialogue_rule(self):
        now = int(time.time() * 1000)
        rules = custom_dialogue_rules(
            [
                {
                    "category": "bug",
                    "tag": "quest_npc_broken",
                    "keywords": ["npc不见了"],
                    "base_severity": "high",
                }
            ]
        )
        builder = RecentWindowBuilder(max_records=2, dialogue_rules=rules)
        for idx in range(10):
            builder.add(
                ObservationRecord(
                    event_id=f"neutral-{idx}",
                    kind="CHAT",
                    timestamp=now - 10000 + idx,
                    server_id="survival",
                    backend_server="s1",
                    player_name=f"Neutral{idx}",
                    player_uuid_hash=f"n-{idx}",
                    content="今天继续建房子",
                )
            )
        builder.add(
            ObservationRecord(
                event_id="custom-issue",
                kind="CHAT",
                timestamp=now,
                server_id="survival",
                backend_server="s1",
                player_name="Alice",
                player_uuid_hash="uuid-Alice",
                content="主城npc不见了，任务断了",
            )
        )

        window = builder.build()

        self.assertTrue(window.truncated)
        self.assertTrue(any(record.player_name == "Alice" for record in window.records))

    def test_dedupe_tracker_spills_to_disk_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = DedupeTracker(max_memory_keys=1, temp_dir=Path(tmpdir))

            self.assertFalse(tracker.seen_or_add("a"))
            self.assertTrue(tracker.seen_or_add("a"))
            self.assertFalse(tracker.seen_or_add("b"))
            self.assertTrue(tracker.spilled)
            spill_path = tracker.path
            self.assertIsNotNone(spill_path)
            self.assertTrue(spill_path.exists())
            self.assertTrue(tracker.seen_or_add("b"))
            tracker.close()
            self.assertFalse(spill_path.exists())

    def test_dedupe_tracker_atomic_multi_key_add_does_not_pollute_seen_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = DedupeTracker(max_memory_keys=1, temp_dir=Path(tmpdir))

            self.assertFalse(tracker.seen_any_or_add_all(("event:a", "content:x")))
            self.assertTrue(tracker.spilled)
            self.assertTrue(tracker.seen_any_or_add_all(("event:b", "content:x")))
            self.assertFalse(tracker.seen_any_or_add_all(("event:b", "content:y")))
            tracker.close()

    def test_dedupe_tracker_hot_cache_skips_sqlite_for_repeated_spill_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = DedupeTracker(max_memory_keys=1, temp_dir=Path(tmpdir))

            self.assertFalse(tracker.seen_or_add("a"))
            self.assertFalse(tracker.seen_or_add("b"))
            self.assertTrue(tracker.spilled)
            self.assertIsNotNone(tracker._conn)

            statements = []
            tracker._conn.set_trace_callback(statements.append)
            try:
                self.assertTrue(tracker.seen_or_add("b"))
                self.assertTrue(tracker.seen_any_or_add_all(("b", "c")))
            finally:
                tracker._conn.set_trace_callback(None)

            self.assertEqual(statements, [])
            self.assertFalse(tracker.seen_or_add("c"))
            tracker.close()

    def test_dedupe_tracker_tuple_fast_path_keeps_multi_key_semantics(self):
        tracker = DedupeTracker(max_memory_keys=10)
        try:
            self.assertFalse(tracker.seen_any_or_add_all(("event:a", "event:a")))
            self.assertTrue(tracker.seen_any_or_add_all(("event:b", "event:a")))
            self.assertFalse(tracker.seen_any_or_add_all(("", "event:b")))
            self.assertTrue(tracker.seen_any_or_add_all(("event:b", "")))
        finally:
            tracker.close()

    def test_jsonl_store_reads_and_exports_full_window(self):
        now = int(time.time() * 1000)
        payload = {
            "serverId": "survival",
            "serverName": "Survival",
            "observations": [
                {
                    "eventId": "evt-1",
                    "kind": "CHAT",
                    "timestamp": now - 1000,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "服务器卡",
                },
                {
                    "eventId": "evt-2",
                    "kind": "CHAT",
                    "timestamp": now,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": "Bob", "uuidHash": "uuid-Bob"},
                    "content": "建议加个传送提示",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            written = store.add_batch("survival", payload)
            records = store.recent(480, "survival")
            export_path = store.export_records(records, 480, "survival", "group:test")

            self.assertEqual(written, 2)
            self.assertEqual([record.player_name for record in records], ["Alice", "Bob"])
            self.assertIsNotNone(export_path)
            self.assertRegex(
                export_path.name,
                r"^mine_sentinel_\d{8}_\d{4}_(?:\d{8}_)?\d{4}_survival\.jsonl$",
            )
            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["player"]["name"], "Alice")

    def test_add_batch_streams_records_to_each_target_file(self):
        now = int(time.time() * 1000)
        yesterday = now - 24 * 60 * 60 * 1000
        old = now - 10 * 24 * 60 * 60 * 1000
        payload = {
            "serverId": "survival",
            "serverName": "Survival",
            "observations": [
                {
                    "eventId": "evt-today",
                    "kind": "CHAT",
                    "timestamp": now,
                    "serverId": "survival",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "今天的聊天",
                },
                {
                    "eventId": "evt-yesterday",
                    "kind": "CHAT",
                    "timestamp": yesterday,
                    "serverId": "survival",
                    "player": {"name": "Bob", "uuidHash": "uuid-Bob"},
                    "content": "昨天的聊天",
                },
                {
                    "eventId": "evt-old",
                    "kind": "CHAT",
                    "timestamp": old,
                    "serverId": "survival",
                    "player": {"name": "Carol", "uuidHash": "uuid-Carol"},
                    "content": "过期聊天",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict(
                    {"storage": {"retention_minutes": 2 * 24 * 60}}
                ),
                Path(tmpdir),
            )

            written = store.add_batch("survival", payload)
            files = sorted((store.observation_dir / "survival").glob("*.jsonl"))

            self.assertEqual(written, 2)
            self.assertEqual(len(files), 2)
            self.assertTrue(all(path.read_text(encoding="utf-8") for path in files))

    def test_add_batch_caches_record_paths_within_same_day(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": f"evt-path-cache-{idx}",
                "kind": "CHAT",
                "timestamp": now + idx,
                "serverId": "survival",
                "player": {"name": f"Player{idx}", "uuidHash": f"uuid-{idx}"},
                "content": f"chat {idx}",
            }
            for idx in range(5)
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(MineSentinelConfig.from_dict({}), Path(tmpdir))

            def fail_if_uncached(*args, **kwargs):
                self.fail("add_batch should use the local record path cache")

            store._record_path = fail_if_uncached
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": observations},
                ),
                5,
            )

    def test_add_batch_throttles_retention_cleanup(self):
        now = int(time.time() * 1000)
        payload = {
            "serverId": "survival",
            "serverName": "Survival",
            "observations": [
                {
                    "eventId": "evt-cleanup-1",
                    "kind": "CHAT",
                    "timestamp": now,
                    "serverId": "survival",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "今天的聊天",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict(
                    {"storage": {"cleanup_interval_seconds": 3600}}
                ),
                Path(tmpdir),
            )
            server_dir = store.observation_dir / "survival"
            server_dir.mkdir(parents=True, exist_ok=True)
            first_old_path = server_dir / "20000101.jsonl"
            first_old_path.write_text("old\n", encoding="utf-8")

            self.assertEqual(store.add_batch("survival", payload), 1)
            self.assertFalse(first_old_path.exists())

            second_old_path = server_dir / "20000102.jsonl"
            second_old_path.write_text("old\n", encoding="utf-8")
            payload["observations"][0]["eventId"] = "evt-cleanup-2"
            payload["observations"][0]["timestamp"] = now + 1

            self.assertEqual(store.add_batch("survival", payload), 1)
            self.assertTrue(second_old_path.exists())
            self.assertFalse(store.cleanup_if_due(time.time()))
            self.assertTrue(second_old_path.exists())
            self.assertTrue(store.cleanup_if_due(time.time() + 3601))
            self.assertFalse(second_old_path.exists())

    def test_recent_window_caps_memory_but_exports_full_window(self):
        now = int(time.time() * 1000)
        observations = []
        for idx in range(5):
            observations.append(
                {
                    "eventId": f"evt-{idx}",
                    "kind": "CHAT",
                    "timestamp": now - (5000 - idx),
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": f"Player{idx}", "uuidHash": f"uuid-{idx}"},
                    "content": f"聊天 {idx}",
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict(
                    {"report": {"max_records_in_memory": 2}}
                ),
                Path(tmpdir),
            )
            store.add_batch(
                "survival",
                {
                    "serverId": "survival",
                    "serverName": "Survival",
                    "observations": observations,
                },
            )

            window = store.recent_window(480, "survival")
            export_path = store.export_recent(480, "survival", "group:test")

            self.assertTrue(window.truncated)
            self.assertEqual(window.total_count, 5)
            self.assertEqual(window.retained_count, 2)
            self.assertEqual(window.unique_players, 5)
            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 5)

    def test_export_recent_reuses_jsonl_lines_after_filtering(self):
        now = int(time.time() * 1000)
        payload = {
            "serverId": "survival",
            "serverName": "Survival",
            "observations": [
                {
                    "eventId": "evt-export-line",
                    "kind": "CHAT",
                    "timestamp": now,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "export me",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(MineSentinelConfig.from_dict({}), Path(tmpdir))
            store.add_batch("survival", payload)
            original = store.codec.json_data_line

            def fail_if_redumped(*args, **kwargs):
                self.fail("export_recent should reuse existing JSONL lines")

            store.codec.json_data_line = fail_if_redumped
            try:
                export_path = store.export_recent(480, "survival", "group:test")
            finally:
                store.codec.json_data_line = original

            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows[0]["eventId"], "evt-export-line")

    def test_recent_reads_skip_expired_jsonl_lines_before_json_loads(self):
        now = int(time.time() * 1000)
        old_row = {
            "eventId": "evt-old-skip-parse",
            "kind": "CHAT",
            "timestamp": now - 10 * 60 * 1000,
            "serverId": "survival",
            "player": {"name": "Old", "uuidHash": "uuid-old"},
            "content": "old",
        }
        recent_row = {
            "eventId": "evt-recent-parse",
            "kind": "CHAT",
            "timestamp": now + 1000,
            "serverId": "survival",
            "player": {"name": "Recent", "uuidHash": "uuid-recent"},
            "content": "recent",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(MineSentinelConfig.from_dict({}), Path(tmpdir))
            server_dir = store.observation_dir / "survival"
            server_dir.mkdir(parents=True)
            day = time.strftime("%Y%m%d", time.localtime(now / 1000))
            path = server_dir / f"{day}.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(old_row, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(recent_row, ensure_ascii=False, separators=(",", ":")),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            original = storage_codec_module.json.loads

            def guarded_loads(line, *args, **kwargs):
                if "evt-old-skip-parse" in line:
                    self.fail("expired JSONL rows should be skipped before json.loads")
                return original(line, *args, **kwargs)

            storage_codec_module.json.loads = guarded_loads
            try:
                window = store.recent_window(1, "survival")
                export_path = store.export_recent(1, "survival", "group:test")
            finally:
                storage_codec_module.json.loads = original

            self.assertEqual(window.total_count, 1)
            exported = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["eventId"] for row in exported], ["evt-recent-parse"])

    def test_recent_window_dedupes_after_spilling_keys_to_disk(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": "evt-1",
                "kind": "CHAT",
                "timestamp": now - 3000,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "服务器卡",
            },
            {
                "eventId": "evt-2",
                "kind": "CHAT",
                "timestamp": now - 2000,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Bob", "uuidHash": "uuid-Bob"},
                "content": "我也卡",
            },
            {
                "eventId": "evt-1",
                "kind": "CHAT",
                "timestamp": now - 1000,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "重复事件",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict(
                    {"storage": {"dedupe_memory_limit": 1}}
                ),
                Path(tmpdir),
            )
            store.add_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )

            window = store.recent_window(480, "survival")
            export_path = store.export_recent(480, "survival", "group:test")

            self.assertEqual(window.total_count, 2)
            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 2)

    def test_recent_window_dedupes_near_duplicate_chat_with_distinct_events(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": "evt-chat-1",
                "kind": "CHAT",
                "timestamp": now - 2000,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "lag!!!",
            },
            {
                "eventId": "evt-chat-2",
                "kind": "CHAT",
                "timestamp": now - 1000,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "lag...",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            store.add_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )

            window = store.recent_window(480, "survival")
            export_path = store.export_recent(480, "survival", "group:test")

            self.assertEqual(window.total_count, 1)
            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)

    def test_add_batch_dedupes_near_duplicate_chat_before_write(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": "evt-chat-1",
                "kind": "CHAT",
                "timestamp": now - 1000,
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "lag!!!",
            },
            {
                "eventId": "evt-chat-2",
                "kind": "CHAT",
                "timestamp": now,
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "lag...",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            written = store.add_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )

            self.assertEqual(written, 1)

    def test_add_batch_skipped_duplicate_does_not_reserve_event_id(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": "evt-chat-1",
                "kind": "CHAT",
                "timestamp": now - 2000,
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "lag!!!",
            },
            {
                "eventId": "evt-chat-2",
                "kind": "CHAT",
                "timestamp": now - 1000,
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "lag...",
            },
            {
                "eventId": "evt-chat-2",
                "kind": "CHAT",
                "timestamp": now,
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "different issue",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            written = store.add_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )

            self.assertEqual(written, 2)

    def test_add_batch_dedupes_near_duplicate_chat_across_batches(self):
        now = int(time.time() * 1000)
        first = {
            "kind": "CHAT",
            "timestamp": now - 1000,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag!!!",
        }
        second = {
            "kind": "CHAT",
            "timestamp": now,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag...",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [first]},
                ),
                1,
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [second]},
                ),
                0,
            )

    def test_add_batch_dedupes_chat_across_dedupe_bucket_boundary(self):
        base = (int(time.time() * 1000) // 1000) * 1000
        first = {
            "kind": "CHAT",
            "timestamp": base + 999,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag!!!",
        }
        second = {
            "kind": "CHAT",
            "timestamp": base + 1001,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag...",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({"dedupe_window_seconds": 1}),
                Path(tmpdir),
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [first]},
                ),
                1,
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [second]},
                ),
                0,
            )

    def test_add_batch_reuses_raw_dedupe_keys_for_ingest_cache(self):
        now = int(time.time() * 1000)
        first = {
            "kind": "CHAT",
            "timestamp": now - 1000,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag!!!",
        }
        second = {
            "kind": "CHAT",
            "timestamp": now,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag...",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )

            def fail_if_recomputed(*args, **kwargs):
                self.fail("raw chat content key should be reused from dedupe keys")

            store.codec.raw_chat_content_dedupe_key = fail_if_recomputed
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [first]},
                ),
                1,
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [second]},
                ),
                0,
            )

    def test_add_batch_keeps_chat_again_after_dedupe_bucket_changes(self):
        now = int(time.time() * 1000)
        first = {
            "kind": "CHAT",
            "timestamp": now,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag!!!",
        }
        second = {
            "kind": "CHAT",
            "timestamp": now + 3000,
            "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
            "content": "lag...",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({"dedupe_window_seconds": 1}),
                Path(tmpdir),
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [first]},
                ),
                1,
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [second]},
                ),
                1,
            )

    def test_recent_window_keeps_distinct_metric_events_in_same_bucket(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": "metric-1",
                "kind": "SERVER_METRICS",
                "timestamp": now - 1000,
                "serverId": "survival",
                "backendServer": "s1",
                "metrics": {"tps1m": 19.8},
            },
            {
                "eventId": "metric-2",
                "kind": "SERVER_METRICS",
                "timestamp": now,
                "serverId": "survival",
                "backendServer": "s1",
                "metrics": {"tps1m": 19.7},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            store.add_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )

            window = store.recent_window(480, "survival")
            export_path = store.export_recent(480, "survival", "group:test")

            self.assertEqual(window.total_count, 2)
            rows = [
                json.loads(line)
                for line in export_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 2)

    def test_add_batch_keeps_distinct_metric_payloads_without_event_ids(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "kind": "SERVER_METRICS",
                "timestamp": now - 1000,
                "serverId": "survival",
                "backendServer": "s1",
                "metrics": {"tps1m": 19.8},
            },
            {
                "kind": "SERVER_METRICS",
                "timestamp": now,
                "serverId": "survival",
                "backendServer": "s1",
                "metrics": {"tps1m": 19.7},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            written = store.add_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )

            self.assertEqual(written, 2)

    def test_add_batch_cross_batch_ingest_cache_ignores_metrics(self):
        now = int(time.time() * 1000)
        first = {
            "kind": "SERVER_METRICS",
            "timestamp": now,
            "serverId": "survival",
            "backendServer": "s1",
            "metrics": {"tps1m": 19.8},
        }
        second = {
            "kind": "SERVER_METRICS",
            "timestamp": now + 1,
            "serverId": "survival",
            "backendServer": "s1",
            "metrics": {"tps1m": 19.8},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [first]},
                ),
                1,
            )
            self.assertEqual(
                store.add_batch(
                    "survival",
                    {"serverId": "survival", "observations": [second]},
                ),
                1,
            )

    def test_raw_chat_content_keys_ignore_non_chat_dedupe_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(MineSentinelConfig.from_dict({}), Path(tmpdir))
            keys = store._raw_chat_content_keys(
                {"kind": "SERVER_METRICS"},
                "survival",
                ("payload|SERVER_METRICS|survival|||abc",),
            )

        self.assertEqual(keys, ())

    def test_recent_window_prioritizes_actionable_chat_when_capped(self):
        now = int(time.time() * 1000)
        observations = []
        for idx in range(20):
            observations.append(
                {
                    "eventId": f"neutral-{idx}",
                    "kind": "CHAT",
                    "timestamp": now - 30000 + idx,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": f"Neutral{idx}", "uuidHash": f"n-{idx}"},
                    "content": "今天继续建房子",
                }
            )
        observations.append(
            {
                "eventId": "issue-loss",
                "kind": "CHAT",
                "timestamp": now - 1000,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                "content": "我的装备没了，能恢复吗",
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict(
                    {"report": {"max_records_in_memory": 2}}
                ),
                Path(tmpdir),
            )
            store.add_batch(
                "survival",
                {
                    "serverId": "survival",
                    "serverName": "Survival",
                    "observations": observations,
                },
            )

            window = store.recent_window(480, "survival")

            self.assertTrue(window.truncated)
            self.assertEqual(window.retained_count, 2)
            self.assertTrue(any(record.player_name == "Alice" for record in window.records))

    def test_recent_window_materializes_only_retained_records(self):
        now = int(time.time() * 1000)
        observations = [
            {
                "eventId": f"evt-lazy-{idx}",
                "kind": "CHAT",
                "timestamp": now - 30000 + idx,
                "serverId": "survival",
                "backendServer": "s1",
                "player": {"name": f"Player{idx}", "uuidHash": f"uuid-{idx}"},
                "content": f"neutral chat {idx}",
            }
            for idx in range(30)
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict(
                    {"report": {"max_records_in_memory": 2}}
                ),
                Path(tmpdir),
            )
            store.add_batch(
                "survival",
                {
                    "serverId": "survival",
                    "serverName": "Survival",
                    "observations": observations,
                },
            )

            calls = 0
            original_descriptor = ObservationRecord.__dict__["from_dict"]
            original = ObservationRecord.from_dict

            def counted_from_dict(data, batch_server_id="", batch_server_name=""):
                nonlocal calls
                calls += 1
                return original(data, batch_server_id, batch_server_name)

            ObservationRecord.from_dict = counted_from_dict
            try:
                window = store.recent_window(480, "survival")
            finally:
                ObservationRecord.from_dict = original_descriptor

            self.assertTrue(window.truncated)
            self.assertEqual(window.retained_count, 2)
            self.assertEqual(calls, 2)

    def test_report_config_parses_memory_cap(self):
        config = MineSentinelConfig.from_dict(
            {"report": {"max_records_in_memory": 123}}
        )

        self.assertEqual(config.report.max_records_in_memory, 123)

    def test_storage_config_parses_cleanup_interval(self):
        config = MineSentinelConfig.from_dict(
            {"storage": {"cleanup_interval_seconds": 7}}
        )
        disabled_throttle = MineSentinelConfig.from_dict(
            {"storage": {"cleanup_interval_seconds": -1}}
        )

        self.assertEqual(config.storage.cleanup_interval_seconds, 7)
        self.assertEqual(disabled_throttle.storage.cleanup_interval_seconds, 0)

    def test_candidate_files_skip_days_before_cutoff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DiskObservationStore(
                MineSentinelConfig.from_dict({}),
                Path(tmpdir),
            )
            server_dir = store.observation_dir / "survival"
            server_dir.mkdir(parents=True, exist_ok=True)
            old_path = server_dir / "20000101.jsonl"
            today_path = server_dir / f"{time.strftime('%Y%m%d')}.jsonl"
            old_path.write_text("old\n", encoding="utf-8")
            today_path.write_text("today\n", encoding="utf-8")

            candidates = store._candidate_files("survival", int(time.time() * 1000))

            self.assertNotIn(old_path, candidates)
            self.assertIn(today_path, candidates)


class MineSentinelReportArtifactTests(unittest.TestCase):
    def test_truncated_window_exports_full_recent_records(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.report_artifacts import (
                MineSentinelReportArtifacts,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.report_artifacts import MineSentinelReportArtifacts

        now = int(time.time() * 1000)
        records = [
            ObservationRecord(
                event_id="evt-1",
                kind="CHAT",
                timestamp=now,
                server_id="survival",
                backend_server="s1",
                player_name="Alice",
                content="服务器卡",
            )
        ]
        window = RecentObservationWindow(
            records=records,
            total_count=5,
            unique_players=3,
            truncated=True,
            max_records=1,
        )
        store = _ArtifactStore(Path("/tmp/full-window.jsonl"))
        artifacts = MineSentinelReportArtifacts(
            MineSentinelConfig.from_dict({}),
            _ArtifactReporter(),
            store,
            thread_runner=_run_artifact_sync,
        )

        report = asyncio.run(
            artifacts.build(
                records,
                480,
                "survival",
                "group:test",
                window,
            )
        )

        self.assertEqual(
            store.export_recent_call,
            {
                "window_minutes": 480,
                "server_id": "survival",
                "label": "group:test",
            },
        )
        self.assertIsNone(store.export_records_call)
        self.assertEqual(report["_export_file_path"], "/tmp/full-window.jsonl")
        self.assertTrue(any("完整窗口 5 条" in note for note in report["ops_notes"]))
        self.assertTrue(
            any("完整聊天记录附件" in note for note in report["ops_notes"])
        )

    def test_report_file_path_ignores_missing_export(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.report_artifacts import (
                MineSentinelReportArtifacts,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.report_artifacts import MineSentinelReportArtifacts

        self.assertIsNone(MineSentinelReportArtifacts.report_file_path({}))


class MineSentinelRoutingTests(unittest.TestCase):
    def test_target_router_dedupes_sessions_and_ignores_missing_config(self):
        records = [
            ObservationRecord(event_id="a", server_id="survival", player_name="Alice"),
            ObservationRecord(event_id="b", server_id="missing", player_name="Bob"),
            ObservationRecord(event_id="c", server_id="", player_name="Carol"),
        ]
        configs = {
            "survival": SimpleNamespace(
                target_sessions=["group:a", "group:b", "group:a", ""]
            )
        }
        router = MineSentinelTargetRouter(lambda sid: configs.get(sid))

        routed = router.records_by_session(records)

        self.assertEqual(sorted(routed), ["group:a", "group:b"])
        self.assertEqual(routed["group:a"], [records[0]])
        self.assertEqual(routed["group:b"], [records[0]])

    def test_delivery_target_shorthand_normalizes_to_napcat_umo(self):
        self.assertEqual(
            normalize_delivery_target("group:123456"),
            "aiocqhttp:GroupMessage:123456",
        )
        self.assertEqual(
            normalize_delivery_target("qq:654321"),
            "aiocqhttp:FriendMessage:654321",
        )
        self.assertEqual(
            normalize_delivery_target("123456"),
            "aiocqhttp:GroupMessage:123456",
        )
        self.assertEqual(
            normalize_delivery_target("aiocqhttp:GroupMessage:999"),
            "aiocqhttp:GroupMessage:999",
        )

    def test_target_router_adds_direct_report_targets(self):
        records = [
            ObservationRecord(event_id="a", server_id="survival", player_name="Alice"),
            ObservationRecord(event_id="b", server_id="survival", player_name="Bob"),
        ]
        router = MineSentinelTargetRouter(
            lambda sid: SimpleNamespace(target_sessions=["group:source"]),
            report_targets=[
                "group:ops",
                "qq:10001",
                "group:ops",
                "aiocqhttp:GroupMessage:source",
            ],
        )

        routed = router.records_by_session(records)

        self.assertEqual(
            sorted(routed),
            [
                "aiocqhttp:FriendMessage:10001",
                "aiocqhttp:GroupMessage:ops",
                "aiocqhttp:GroupMessage:source",
                "group:source",
            ],
        )
        self.assertEqual(routed["aiocqhttp:GroupMessage:ops"], records)
        self.assertEqual(
            router.sessions_for_records(
                records,
                exclude_session="aiocqhttp:GroupMessage:source",
            ),
            ["aiocqhttp:FriendMessage:10001", "aiocqhttp:GroupMessage:ops", "group:source"],
        )

        self.assertEqual(
            sorted(
                router.records_by_session(
                    records,
                    include_server_targets=False,
                )
            ),
            [
                "aiocqhttp:FriendMessage:10001",
                "aiocqhttp:GroupMessage:ops",
                "aiocqhttp:GroupMessage:source",
            ],
        )

    def test_dispatcher_excludes_current_session_and_reports_send_error(self):
        asyncio.run(self._run_dispatcher_flow())

    async def _run_dispatcher_flow(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.dispatch import (
                MineSentinelReportDispatcher,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.dispatch import MineSentinelReportDispatcher

        record = ObservationRecord(
            event_id="a",
            server_id="survival",
            player_name="Alice",
        )
        router = MineSentinelTargetRouter(
            lambda sid: SimpleNamespace(target_sessions=["group:source", "group:ops"])
        )
        delivery = _FailingDelivery(fail_session="group:ops")
        errors = []
        dispatcher = MineSentinelReportDispatcher(delivery, router, errors.append)

        await dispatcher.send_to_target_sessions(
            "report",
            [record],
            current_session="group:source",
        )

        self.assertEqual(delivery.sent, [("group:ops", "report", None)])
        self.assertEqual(errors, ["发送报告到 group:ops 失败"])


class MineSentinelServiceTests(unittest.TestCase):
    def test_report_now_reads_disk_and_returns_dialogue_findings(self):
        asyncio.run(self._run_report_now_flow())

    def test_report_now_notes_bounded_memory_and_exports_full_file(self):
        asyncio.run(self._run_bounded_report_flow())

    def test_report_console_log_renders_real_excerpt(self):
        asyncio.run(self._run_console_log_report_flow())

    def test_mclogs_raw_url_normalizes_supported_links(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.service import (
                mclogs_raw_url,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.service import mclogs_raw_url

        self.assertEqual(
            mclogs_raw_url("https://mclo.gs/pbfhCaI"),
            "https://api.mclo.gs/1/raw/pbfhCaI",
        )
        self.assertEqual(
            mclogs_raw_url("https://api.mclo.gs/1/raw/pbfhCaI"),
            "https://api.mclo.gs/1/raw/pbfhCaI",
        )
        self.assertIsNone(mclogs_raw_url("https://example.com/pbfhCaI"))

    async def _run_report_now_flow(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.service import (
                MineSentinelService,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.service import MineSentinelService

        now = int(time.time() * 1000)
        payload = {
            "serverId": "survival",
            "observations": [
                {
                    "eventId": "evt-1",
                    "kind": "CHAT",
                    "timestamp": now - 2000,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": "Alice", "uuidHash": "uuid-Alice"},
                    "content": "服务器卡到玩不了",
                },
                {
                    "eventId": "evt-2",
                    "kind": "CHAT",
                    "timestamp": now - 1000,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": "Bob", "uuidHash": "uuid-Bob"},
                    "content": "我也 lag，一直延迟",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            context = _DummyContext()
            service = MineSentinelService(
                context=context,
                config_data={
                    "report": {
                        "send_full_log_file": True,
                        "send_to_target_sessions": False,
                    }
                },
                get_server_config=lambda sid: SimpleNamespace(target_sessions=[]),
                storage_dir=Path(tmpdir),
                io_runner=_run_artifact_sync,
            )

            await service.handle_batch("survival", payload)
            text = await service.report_now("group:test", "survival", 480)

            self.assertIn("完整聊天记录：已保存为附件 mine_sentinel_", text)
            self.assertIn("二、聊天与事件总结", text)
            self.assertIn("三、玩家问题/投诉识别", text)
            self.assertIn("Alice", text)
            self.assertIn("Bob", text)
            self.assertTrue(any(_chain_has_file(chain) for _, chain in context.sent))

    async def _run_bounded_report_flow(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.service import (
                MineSentinelService,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.service import MineSentinelService

        now = int(time.time() * 1000)
        observations = []
        for idx, player in enumerate(("Alice", "Bob", "Carol")):
            observations.append(
                {
                    "eventId": f"bounded-{idx}",
                    "kind": "CHAT",
                    "timestamp": now - 3000 + idx,
                    "serverId": "survival",
                    "backendServer": "s1",
                    "player": {"name": player, "uuidHash": f"uuid-{player}"},
                    "content": f"{player} 说服务器有点卡",
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            context = _DummyContext()
            service = MineSentinelService(
                context=context,
                config_data={
                    "report": {
                        "max_records_in_memory": 1,
                        "send_full_log_file": True,
                        "send_to_target_sessions": False,
                    }
                },
                get_server_config=lambda sid: SimpleNamespace(target_sessions=[]),
                storage_dir=Path(tmpdir),
                io_runner=_run_artifact_sync,
            )

            await service.handle_batch(
                "survival",
                {"serverId": "survival", "observations": observations},
            )
            text = await service.report_now("group:test", "survival", 480)
            exported_paths = _chain_file_paths(context.sent[0][1])

            self.assertIn("有界样本", text)
            self.assertIn("完整窗口 3 条", text)
            rows = [
                json.loads(line)
                for line in Path(exported_paths[0]).read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(rows), 3)

    async def _run_console_log_report_flow(self):
        _install_astrbot_stubs()
        try:
            from astrbot_plugin_minecraft_adapter.services.mine_sentinel.service import (
                MineSentinelService,
            )
        except ModuleNotFoundError:
            from services.mine_sentinel.service import MineSentinelService

        fixture = Path(__file__).parent / "fixtures" / "mclogs_pbfhCaI_excerpt.log"
        raw_log = fixture.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            service = MineSentinelService(
                context=_DummyContext(),
                config_data={
                    "report": {
                        "send_as_image": False,
                        "send_full_log_file": False,
                        "send_to_target_sessions": False,
                    }
                },
                get_server_config=lambda sid: SimpleNamespace(target_sessions=[]),
                storage_dir=Path(tmpdir),
                io_runner=_run_artifact_sync,
            )

            result = await service.report_console_log_result(
                current_session="group:test",
                source=raw_log,
                server_id="spawn",
            )

            self.assertIsNone(result.image)
            self.assertIn("一、整体情况", result.text)
            self.assertIn("二、聊天与事件总结", result.text)
            self.assertIn("三、玩家问题/投诉识别", result.text)
            self.assertIn("四、风险提醒", result.text)
            self.assertIn("五、建议处理", result.text)
            self.assertIn("服务器安全配置风险", result.text)
            self.assertIn("数据转换/NBT 错误", result.text)
            self.assertIn("运维排查", result.text)


class _ArtifactReporter:
    async def build_report(self, records, window_minutes, server_id=None, umo=None):
        return {
            "summary": f"{len(records)} records",
            "time_window": f"最近 {window_minutes} 分钟",
            "servers": [server_id] if server_id else [],
            "chat_count": 0,
            "chat_players": [],
            "chat_players_text": "未知",
            "dialogue_findings": [],
            "categories": {
                "daily": [],
                "complaint": [],
                "bug": [],
                "economy": [],
                "moderation": [],
                "suggestion": [],
                "cross_server": [],
            },
            "issues": [],
            "ops_notes": [],
        }


class _ArtifactStore:
    def __init__(self, export_path: Path):
        self.export_path = export_path
        self.export_recent_call = None
        self.export_records_call = None

    def export_recent(self, window_minutes, server_id, label):
        self.export_recent_call = {
            "window_minutes": window_minutes,
            "server_id": server_id,
            "label": label,
        }
        return self.export_path

    def export_records(self, records, window_minutes, server_id, label):
        self.export_records_call = {
            "record_count": len(records),
            "window_minutes": window_minutes,
            "server_id": server_id,
            "label": label,
        }
        return self.export_path


async def _run_artifact_sync(func, *args):
    return func(*args)


class _DummyContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))

    def get_using_provider(self, *args):
        return None


class _FailingDelivery:
    def __init__(self, fail_session: str = ""):
        self.fail_session = fail_session
        self.last_error = ""
        self.sent = []

    async def send_message(self, umo, text, file_path=None):
        self.sent.append((umo, text, file_path))
        if umo == self.fail_session:
            self.last_error = f"发送报告到 {umo} 失败"
            return False
        return True

    async def send_file(self, umo, file_path):
        self.sent.append((umo, file_path))
        return True


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

    class MessageChain(list):
        pass

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class File:
        def __init__(self, file=""):
            self.file = file

    astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
    api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
    if not hasattr(api, "logger"):
        api.logger = Logger()
    event = types.ModuleType("astrbot.api.event")
    event.MessageChain = MessageChain
    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = Plain
    components.File = File
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.message_components": components,
        }
    )


def _chain_has_file(chain) -> bool:
    return any(hasattr(component, "file") for component in chain)


def _chain_file_paths(chain) -> list[str]:
    return [
        component.file
        for component in chain
        if getattr(component, "file", "")
    ]


if __name__ == "__main__":
    unittest.main()
