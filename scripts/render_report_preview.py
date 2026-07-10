"""Generate the README preview for the incident-management report layout."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import astrbot  # noqa: F401
except ModuleNotFoundError:
    from tests.astrbot_stubs import ensure_test_import_paths, install_astrbot_stubs
    from tests.mine_sentinel_rs_stub import install_mine_sentinel_rs_stub_if_missing

    ensure_test_import_paths()
    install_astrbot_stubs()
    install_mine_sentinel_rs_stub_if_missing()

from services.mine_sentinel.reporting.image_renderer import (
    MineSentinelReportImageRenderer,
)
from services.mine_sentinel.reporting.incident_management import (
    IncidentManagementBuilder,
)


def preview_report() -> dict:
    return {
        "servers": ["survival", "velocity"],
        "server_names": ["生存服", "代理服"],
        "window_start_ts": 1783515125000,
        "window_end_ts": 1783517386000,
        "_window_minutes": 38,
        "_export_file_name": "minesentinel-evidence-20260710.jsonl.gz",
        "chat_summary": "社区聊天整体平稳，存在一项玩家反馈需要回访。",
        "categories": {},
        "report_sections": [
            {"id": "overall", "title": "一、整体情况", "bullets": ["兼容结构保留"]},
            {"id": "incidents", "title": "二、重点事件总结", "bullets": []},
            {"id": "community", "title": "三、聊天与社区观察", "bullets": []},
            {
                "id": "player_problems",
                "title": "四、玩家问题/投诉识别",
                "bullets": [],
            },
            {"id": "risk_actions", "title": "五、风险提醒与建议处理", "bullets": []},
        ],
        "issues": [
            {
                "category": "plugin",
                "tag": "server_log_plugin",
                "title": "QuickShop 数据库连接持续超时",
                "severity": "high",
                "should_alert": True,
                "evidence_count": 14,
                "first_seen_ts": 1783515125000,
                "last_seen_ts": 1783515485000,
                "affected_servers": ["survival"],
                "affected_locations": ["survival"],
                "affected_plugins": ["QuickShop"],
                "affected_log_files": ["latest.log"],
                "ops_subtypes": ["数据库超时", "经济/商店异常"],
                "suggested_action": (
                    "先检查 MariaDB 连通性和 QuickShop 连接池，再暂停高风险经济变更并保留失败请求证据。"
                ),
                "evidence_samples": [
                    "[12:52:05] WARN QuickShop database connection timeout after 30000ms",
                    "[12:54:16] ERROR Failed to save shop transaction; retry scheduled",
                    "[12:58:05] WARN HikariPool connection is not available",
                ],
            },
            {
                "category": "player_feedback",
                "tag": "server_log_player_feedback",
                "title": "跨服后背包状态异常反馈",
                "severity": "medium",
                "should_alert": True,
                "evidence_count": 5,
                "first_seen_ts": 1783516900000,
                "last_seen_ts": 1783517140000,
                "affected_servers": ["velocity", "survival"],
                "affected_locations": ["velocity/lobby -> survival"],
                "affected_plugins": ["Velocity", "PlayerDataSync"],
                "affected_log_files": ["velocity.log", "latest.log"],
                "affected_worlds": ["world"],
                "affected_positions": ["world (128, 65, -42)"],
                "players": ["示例玩家_青禾", "示例玩家_星河"],
                "chat_labels": ["跨服异常", "物品异常"],
                "suggested_action": (
                    "关联代理切服日志与玩家数据保存记录，回访受影响玩家并完成一次受控复现。"
                ),
                "evidence_samples": [
                    "[13:21:40] <示例玩家_青禾> 从大厅进生存服后背包少了刚才的东西",
                    "[13:25:40] <示例玩家_星河> 我也遇到了，重新进服还没恢复",
                ],
            },
        ],
    }


async def main() -> None:
    report = preview_report()
    IncidentManagementBuilder().attach(
        report,
        1268,
        31,
        47,
        report_type="periodic",
        state_scope="preview",
    )
    report["incident_management"]["report_type_label"] = "报告样例（虚构数据）"
    renderer = MineSentinelReportImageRenderer(
        ROOT / ".preview-cache",
        max_summary_incidents=6,
        max_detail_pages=6,
    )
    pages = await renderer.render_pages(report, 1268, 31, 47)
    output_dir = ROOT / "docs" / "report-preview"
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages, 1):
        path = output_dir / f"minesentinel-incident-management-v3-page-{index}.png"
        path.write_bytes(page.getvalue())
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    asyncio.run(main())
