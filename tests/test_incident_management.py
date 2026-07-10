from __future__ import annotations

import asyncio
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

try:
    from tests.astrbot_stubs import ensure_test_import_paths, install_astrbot_stubs
    from tests.mine_sentinel_rs_stub import install_mine_sentinel_rs_stub_if_missing
except ModuleNotFoundError:
    from astrbot_stubs import ensure_test_import_paths, install_astrbot_stubs
    from mine_sentinel_rs_stub import install_mine_sentinel_rs_stub_if_missing


ensure_test_import_paths()
install_astrbot_stubs()
install_mine_sentinel_rs_stub_if_missing()

from services.mine_sentinel.delivery import MineSentinelDelivery
from services.mine_sentinel.alerts import MineSentinelAlertEngine
from services.mine_sentinel.models import MineSentinelConfig, ObservationRecord
from services.mine_sentinel.reporting.common import plugin_name_list
from services.mine_sentinel.reporting.ai_diagnosis import AIContextLocator
from services.mine_sentinel.reporting.ai_prompt import AIReportPromptBuilder
from services.mine_sentinel.reporting.incident_response import (
    build_check_plan,
    build_incident_facts,
)
from services.mine_sentinel.reporting.incidents import IncidentGrouper
from services.mine_sentinel.reporting.labels import (
    action_label,
    action_timing,
    impact_label,
)
from services.mine_sentinel.reporting.incident_management import (
    IncidentLifecycleStore,
    IncidentManagementBuilder,
    format_incident_management_text,
)
from services.mine_sentinel.reporting.report_result import MineSentinelRenderedReport


def _report(first_seen: int, last_seen: int, include_issue: bool = True) -> dict:
    issues = []
    if include_issue:
        issues.append(
            {
                "category": "plugin",
                "tag": "server_log_plugin",
                "title": "QuickShop 数据库连接超时",
                "severity": "high",
                "should_alert": True,
                "evidence_count": 4,
                "first_seen_ts": first_seen,
                "last_seen_ts": last_seen,
                "affected_servers": ["survival"],
                "affected_locations": ["survival/latest.log"],
                "affected_plugins": ["QuickShop"],
                "affected_log_files": ["latest.log"],
                "affected_worlds": ["world"],
                "affected_positions": ["world (120, 64, -32)"],
                "players": ["PlayerA"],
                "ops_subtypes": ["数据库超时"],
                "suggested_action": "检查数据库连通性和 QuickShop 连接池配置。",
                "evidence_samples": ["[12:00:00] WARN database timeout"],
            }
        )
    return {
        "servers": ["survival"],
        "issues": issues,
        "categories": {},
        "report_sections": [{"id": "overall", "bullets": ["legacy"]}],
        "window_start_ts": first_seen - 60_000,
        "window_end_ts": last_seen + 60_000,
        "_export_file_name": "evidence.jsonl.gz",
    }


class IncidentManagementTests(unittest.TestCase):
    def test_lifecycle_moves_from_new_to_ongoing_to_recovered(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            builder = IncidentManagementBuilder(
                IncidentLifecycleStore(Path(tmp_dir) / "lifecycle.json")
            )
            first = builder.build(
                _report(1_000_000, 1_060_000),
                4,
                0,
                1,
                report_type="periodic",
                state_scope="group:1",
                persist_state=True,
            )
            self.assertEqual(first["counts"]["new"], 1)
            self.assertEqual(first["incidents"][0]["lifecycle"], "new")

            second = builder.build(
                _report(1_070_000, 1_120_000),
                5,
                0,
                1,
                report_type="periodic",
                state_scope="group:1",
                persist_state=True,
            )
            self.assertEqual(second["counts"]["ongoing"], 1)
            self.assertEqual(
                second["incidents"][0]["incident_id"],
                first["incidents"][0]["incident_id"],
            )

            third = builder.build(
                _report(1_130_000, 1_180_000, include_issue=False),
                2,
                0,
                0,
                report_type="periodic",
                state_scope="group:1",
                persist_state=True,
            )
            self.assertEqual(third["counts"]["recovered"], 1)
            self.assertEqual(third["status_level"], "recovered")

    def test_management_text_is_action_first_and_keeps_evidence_provenance(self):
        report = _report(1_000_000, 1_060_000)
        builder = IncidentManagementBuilder()
        builder.attach(report, 4, 0, 1)

        text = format_incident_management_text(report)

        self.assertLess(text.index("接下来要做什么"), text.index("处理进度"))
        self.assertIn("完成标准", text)
        self.assertIn("时间：", text)
        self.assertIn("地点：", text)
        self.assertIn("人物：PlayerA", text)
        self.assertIn("插件/组件：QuickShop", text)
        self.assertIn("日志：latest.log", text)
        self.assertIn("通过标准：", text)
        self.assertIn("未通过：", text)
        self.assertIn("evidence.jsonl.gz", text)
        self.assertIn("马上处理", text)
        self.assertNotRegex(text, r"\bP[0-4]\b")
        self.assertEqual(report["report_sections"][0]["id"], "overall")
        self.assertEqual(report["incident_management"]["schema_version"], 3)
        summary_action = report["incident_management"]["action_queue"][0]["action"]
        self.assertIn("服务器维护人员", summary_action)
        self.assertNotIn("SELECT 1", summary_action)

    def test_reader_labels_replace_technical_priority_codes(self):
        self.assertEqual(action_label("critical"), "马上处理")
        self.assertEqual(action_label("medium"), "今天处理")
        self.assertEqual(action_label("low"), "留意观察")
        self.assertEqual(action_timing("medium"), "今天内")
        self.assertEqual(impact_label("high"), "很可能影响正常使用")

    def test_postmortem_uses_review_action_language(self):
        report = _report(1_000_000, 1_060_000)
        builder = IncidentManagementBuilder()
        builder.attach(report, 4, 0, 1, report_type="postmortem")

        text = format_incident_management_text(report)

        self.assertIn("MineSentinel 事件复盘", text)
        self.assertIn("复盘后要做什么", text)
        self.assertIn("首要事件发生于", text)
        self.assertIn("涉及 QuickShop，人物 PlayerA", text)

    def test_summary_incident_limit_applies_to_text_and_actions(self):
        report = _report(1_000_000, 1_060_000)
        second = dict(report["issues"][0])
        second.update(
            {
                "tag": "server_log_network",
                "title": "代理连接超时",
                "affected_servers": ["velocity"],
                "affected_locations": ["velocity/latest.log"],
            }
        )
        report["issues"].append(second)
        builder = IncidentManagementBuilder(max_summary_incidents=1)
        builder.attach(report, 8, 0, 1)

        management = report["incident_management"]
        text = format_incident_management_text(report)

        self.assertEqual(len(management["action_queue"]), 1)
        self.assertIn("另有 1 个事件", text)

    def test_repeated_same_kind_events_receive_distinct_stable_ids(self):
        report = _report(1_000_000, 1_060_000)
        repeated = dict(report["issues"][0])
        repeated.update(
            {
                "first_seen_ts": 5_000_000,
                "last_seen_ts": 5_060_000,
            }
        )
        report["issues"].append(repeated)

        first = IncidentManagementBuilder().build(report, 8, 0, 1)
        second = IncidentManagementBuilder().build(report, 8, 0, 1)
        first_ids = [item["incident_id"] for item in first["incidents"]]
        second_ids = [item["incident_id"] for item in second["incidents"]]

        self.assertEqual(len(first_ids), 2)
        self.assertEqual(len(first_ids), len(set(first_ids)))
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(all(len(incident_id) == 15 for incident_id in first_ids))

    def test_report_result_keeps_first_page_compatibility(self):
        pages = [BytesIO(b"summary"), BytesIO(b"detail")]
        result = MineSentinelRenderedReport("text", images=pages)

        self.assertIs(result.image, pages[0])
        self.assertEqual(len(result.images), 2)

    def test_config_defaults_to_incident_management_layout(self):
        config = MineSentinelConfig.from_dict({})

        self.assertEqual(config.report.layout, "incident_management")
        self.assertEqual(config.report.max_detail_pages, 6)

    def test_database_plan_is_specific_and_has_pass_fail_paths(self):
        issue = _report(1_000_000, 1_060_000)["issues"][0]
        issue["ops_categories"] = ["数据库与存储", "经济与资产"]
        issue["ops_subtypes"] = ["数据库超时", "经济/商店异常"]
        facts = build_incident_facts([issue])

        plan = build_check_plan([issue], facts, "operations")
        plan_text = "\n".join(str(step) for step in plan)

        self.assertEqual(facts["time"], "1970-01-01 08:16:40 - 08:17:40")
        self.assertEqual(facts["where"], "survival/latest.log / world (120, 64, -32)")
        self.assertEqual(facts["people_text"], "PlayerA")
        self.assertEqual(facts["components"], "QuickShop")
        self.assertIn("SELECT 1", plan_text)
        self.assertIn("maxLifetime", plan_text)
        self.assertIn("余额变更", plan_text)
        self.assertEqual(plan[-1]["phase"], "恢复验证")

    def test_economy_external_timeout_does_not_misroute_to_database_plan(self):
        issue = _report(1_000_000, 1_060_000)["issues"][0]
        issue.update(
            {
                "category": "economy",
                "ops_categories": ["经济与资产"],
                "ops_subtypes": ["经济/商店异常"],
                "evidence_samples": [
                    "Connect to crowdinota.quickshop.example:443 failed: Connect timed out"
                ],
            }
        )
        facts = build_incident_facts([issue])

        plan = build_check_plan([issue], facts, "operations")
        plan_text = "\n".join(str(step) for step in plan)

        self.assertEqual(
            facts["external_services"],
            ["crowdinota.quickshop.example:443"],
        )
        self.assertIn("交易主链路还是可选资源服务", plan_text)
        self.assertIn("DNS、TCP/TLS 和 HTTP", plan_text)
        self.assertNotIn("SELECT 1", plan_text)
        self.assertEqual(plan[-1]["phase"], "恢复验证")

    def test_technical_incidents_do_not_merge_across_failure_domains(self):
        base = _report(1_000_000, 1_060_000)["issues"][0]
        network = dict(base)
        network.update(
            {
                "category": "network",
                "tag": "server_log_network",
                "ops_categories": ["网络与代理"],
                "ops_subtypes": ["网络连接异常"],
            }
        )

        groups = IncidentGrouper().group([base, network])

        self.assertEqual(len(groups), 2)

    def test_plugin_names_are_extracted_from_real_log_prefixes(self):
        records = [
            ObservationRecord(
                content=(
                    "[21:21:24] [Craft Scheduler Thread - 144 - UltimateRewards/WARN]: "
                    "[eu.athelion.pool.PoolBase] UltimateRewards-Pool failed"
                ),
                context={"logFile": "latest.log"},
            ),
            ObservationRecord(
                content=(
                    "[21:15:46] [luckperms-worker-11/WARN]: "
                    "luckperms-hikari failed"
                ),
                context={"logFile": "latest.log"},
            ),
            ObservationRecord(
                content="[20:54:10] Cannot load plugins\\PlayerTitle\\notify.yml",
                context={"logFile": "latest.log"},
            ),
        ]

        self.assertEqual(
            plugin_name_list(records),
            ["UltimateRewards", "luckperms", "PlayerTitle"],
        )

    def test_rules_collect_people_from_structured_context(self):
        from services.mine_sentinel.reporting.common import person_name_list

        records = [
            ObservationRecord(
                player_name="PlayerA",
                context={"targetPlayer": "PlayerB", "sender": "CONSOLE"},
            ),
            ObservationRecord(context={"vulcanPlayer": "PlayerC"}),
        ]

        self.assertEqual(person_name_list(records), ["PlayerA", "PlayerB", "PlayerC"])

    def test_person_name_is_extracted_from_native_minecraft_warning(self):
        from services.mine_sentinel.reporting.common import person_name_list

        records = [
            ObservationRecord(
                content=(
                    "[20:14:22] [Server thread/WARN]: "
                    "_Czser moved wrongly!, (0.0)"
                )
            )
        ]

        self.assertEqual(person_name_list(records), ["_Czser"])

    def test_ai_prompts_receive_operator_fact_fields(self):
        config = MineSentinelConfig.from_dict({})
        record = ObservationRecord(
            event_id="db-1",
            kind="SERVER_LOG",
            timestamp=1_000_000,
            server_id="survival",
            content="[WARN] [QuickShop-Hikari] connection timeout",
            context={"level": "WARN", "logFile": "latest.log"},
        )
        issue = _report(1_000_000, 1_060_000)["issues"][0]
        issue["evidence_samples"] = [record.evidence_text()]

        payload = AIContextLocator(config).issue_payload(0, issue, [record])
        prompt = AIReportPromptBuilder(config).build(
            [record],
            30,
            {"issues": [issue], "servers": ["survival"]},
        )

        self.assertEqual(payload["affected_plugins"], ["QuickShop"])
        self.assertEqual(payload["affected_log_files"], ["latest.log"])
        self.assertEqual(payload["affected_worlds"], ["world"])
        self.assertEqual(payload["affected_positions"], ["world (120, 64, -32)"])
        self.assertIn('"affected_plugins": ["QuickShop"]', prompt)
        self.assertIn("不得编造插件、玩家、世界、坐标或文件", prompt)

    def test_ai_review_is_exposed_in_management_report(self):
        report = _report(1_000_000, 1_060_000)
        issue = report["issues"][0]
        issue["ai_assessment"] = "AI 已核对当前证据，但根因仍需复现确认。"
        issue["suggested_action"] = "先复现受影响功能，再核对对应插件设置。"
        issue["ai_diagnosis"] = {
            "confidence": 0.8,
            "evidence_record_indexes": [0],
        }
        issue["ai_check_plan"] = [
            {
                "phase": "复现确认",
                "check": "联系 PlayerA 确认操作步骤并用测试账号复现。",
                "expected": "得到可重复步骤或确认只是单次误操作。",
                "on_failure": "补充客户端提示和同一时间前后的日志后再判断。",
            },
            {
                "phase": "恢复验证",
                "check": "修正后由 PlayerA 和测试账号各验证一次。",
                "expected": "两次测试均成功，并且没有再次出现同类问题。",
                "on_failure": "保持事件为处理中并交给对应插件负责人。",
            },
        ]

        IncidentManagementBuilder().attach(report, 4, 0, 1)
        incident = report["incident_management"]["incidents"][0]
        text = format_incident_management_text(report)

        self.assertTrue(incident["ai_reviewed"])
        self.assertTrue(incident["ai_plan_used"])
        self.assertEqual(incident["check_plan_source"], "ai")
        self.assertEqual(incident["check_plan"], issue["ai_check_plan"])
        self.assertEqual(incident["action_now"], issue["ai_check_plan"][0]["check"])
        self.assertIn("AI 已核对当前证据", incident["assessment"])
        self.assertIn("AI 复核建议", text)
        self.assertIn("AI 给出的检查与解决方案", text)
        self.assertIn("先复现受影响功能", text)

    def test_incomplete_ai_plan_falls_back_to_rule_plan(self):
        report = _report(1_000_000, 1_060_000)
        issue = report["issues"][0]
        issue["ai_diagnosis"] = {"confidence": 0.5}
        issue["ai_check_plan"] = [
            {
                "phase": "只有一步",
                "check": "检查数据库。",
                "expected": "数据库正常。",
                "on_failure": "继续调查。",
            }
        ]

        IncidentManagementBuilder().attach(report, 4, 0, 1)
        incident = report["incident_management"]["incidents"][0]

        self.assertTrue(incident["ai_reviewed"])
        self.assertFalse(incident["ai_plan_used"])
        self.assertEqual(incident["check_plan_source"], "rules")
        self.assertGreaterEqual(len(incident["check_plan"]), 2)
        self.assertEqual(incident["check_plan"][-1]["phase"], "恢复验证")

    def test_instant_alert_includes_facts_and_concrete_checks(self):
        config = MineSentinelConfig.from_dict(
            {"alert": {"enabled": True, "cooldown_seconds": 0}}
        )
        issue = _report(1_000_000, 1_060_000)["issues"][0]
        issue.update(
            {
                "should_alert": True,
                "ops_categories": ["数据库与存储"],
                "ops_subtypes": ["数据库超时"],
            }
        )

        message = MineSentinelAlertEngine(config).build_messages(
            "survival", {"issues": [issue]}
        )[0]

        self.assertIn("时间：1970-01-01 08:16:40 - 08:17:40", message)
        self.assertIn("人物：PlayerA", message)
        self.assertIn("插件/组件：QuickShop", message)
        self.assertIn("日志文件：latest.log", message)
        self.assertIn("SELECT 1", message)
        self.assertIn("通过标准：", message)
        self.assertIn("未通过：", message)
        self.assertIn("处理要求：马上处理（现在开始）", message)
        self.assertIn("影响判断：很可能影响正常使用", message)
        self.assertNotRegex(message, r"\bP[0-4]\b")


class DeliveryPageTests(unittest.TestCase):
    def test_delivery_sends_all_pages_before_attachment(self):
        calls: list[str] = []
        delivery = MineSentinelDelivery(object())

        async def send_image(_umo, image):
            calls.append(image.getvalue().decode())
            return True

        async def send_file(_umo, _path):
            calls.append("file")
            return True

        delivery.send_image = send_image
        delivery.send_file = send_file
        pages = [BytesIO(b"summary"), BytesIO(b"detail")]

        sent = asyncio.run(
            delivery.send_report(
                "group:1",
                "fallback",
                file_path=Path("evidence.jsonl"),
                images=pages,
            )
        )

        self.assertTrue(sent)
        self.assertEqual(calls, ["summary", "detail", "file"])


if __name__ == "__main__":
    unittest.main()
