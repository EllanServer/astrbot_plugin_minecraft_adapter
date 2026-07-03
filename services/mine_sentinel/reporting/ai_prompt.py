"""Prompt construction for AI-assisted MineSentinel reports."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .common import MAX_DISPLAY_PLAYERS, format_players, player_name_list
from .sampling import even_sample, sample_records_for_ai


MAX_EVIDENCE_SAMPLE_CHARS = 520
MAX_CONTEXT_LINE_CHARS = 180
MAX_CONTEXT_LINES = 5


class AIReportPromptBuilder:
    """Builds bounded prompts from complete-window deterministic facts."""

    def __init__(self, config: MineSentinelConfig):
        self.config = config

    def build(
        self,
        records: list[ObservationRecord],
        window_minutes: int,
        fallback: dict[str, Any],
    ) -> str:
        fallback_json = json.dumps(self.compact_fallback(fallback), ensure_ascii=False)
        timeline = self.timeline_chunks(records)
        compact_records = [
            self.compact_record(record)
            for record in self.sample_for_ai(records, fallback)
        ]
        return self.fit_prompt(
            window_minutes,
            fallback_json,
            timeline,
            compact_records,
        )

    def fit_prompt(
        self,
        window_minutes: int,
        fallback_json: str,
        timeline_chunks: list[dict[str, Any]],
        compact_records: list[dict[str, Any]],
    ) -> str:
        max_chars = self.config.report.max_ai_prompt_chars
        records = list(compact_records)
        chunks = [dict(chunk) for chunk in timeline_chunks]

        while True:
            prompt = self.prompt_text(window_minutes, fallback_json, chunks, records)
            if len(prompt) <= max_chars:
                return prompt
            if records:
                records = even_sample(records, max(0, len(records) * 3 // 4))
                continue
            if self.drop_chunk_samples(chunks):
                continue
            if len(chunks) > 1:
                chunks = even_sample(chunks, max(1, len(chunks) // 2))
                continue
            return prompt[:max_chars]

    @staticmethod
    def prompt_text(
        window_minutes: int,
        fallback_json: str,
        timeline_chunks: list[dict[str, Any]],
        compact_records: list[dict[str, Any]],
    ) -> str:
        return (
            "你是 Minecraft 服务器只读旁路监控 MineSentinel 的报告代理。"
            "只输出合法 JSON，不要执行任何管理动作。"
            "必须使用以下 schema: summary,time_window,servers,chat_count,chat_players,"
            "dialogue_findings,"
            "categories(daily,complaint,bug,economy,moderation,suggestion,cross_server),"
            "issues(category,tag,incident_index,severity,players,mentioned_players,"
            "affected_locations,dialogue_terms,metric_context_text,"
            "evidence_count,signal_count,unique_players,"
            "suggested_action),"
            "ops_notes。"
            "输入里的启发式初稿来自完整窗口记录；分段时间线也是完整窗口的压缩统计；"
            "issues.evidence_samples 若包含多行上下文，> 行是命中的证据聊天，"
            "其余行是同服/同后端前后文；"
            "抽样观察里的 context 是来源、消息类型、世界/维度和后端服线索，"
            "只能辅助判断前因后果；"
            "原始样本只用于补措辞，不代表全部记录。"
            "这些 JSON 字段会被组装为 QQ 群五段式总结："
            "整体情况、聊天与事件总结、玩家问题/投诉识别、风险提醒、建议处理；"
            "请让 summary、dialogue_findings、categories 和 suggested_action 面向玩家群可读，"
            "保留具体玩家名、时间线索、上下文结论和人工处理建议。"
            "生成聊天与事件相关内容时必须按事故聚合，而不是按问题类别拆分："
            "同一服务器、同一世界或后端、同一 3 到 5 分钟窗口内的多条异常反馈，"
            "应优先合并为一个 incident，并在该 incident 内列出多个标签和影响面；"
            "不要把卡顿/延迟、掉线/回档、传送异常、经济/商店异常等类别各自写成独立事件，"
            "除非它们发生在不同时间、不同服务器/后端、不同玩家上下文或明显属于不同事故；"
            "incident_index 只能表示真实事故序号，同一批上下文不要重复输出多个 事件 #1；"
            "server_metrics 不要作为聊天事件单独写入 issues 或 dialogue_findings，"
            "服务器指标只用于 metric_context_text、ops_notes、风险提醒或解释玩家反馈；"
            "每个事故最多保留 2 到 4 条关键证据，避免在多个事件中重复粘贴同一批上下文；"
            "如果窗口内只有一个明显异常时间点，应说明其他时间段未发现明显持续异常。"
            f"时间窗口: 最近 {window_minutes} 分钟。\n"
            f"启发式初稿: {fallback_json}\n"
            f"分段时间线: {json.dumps(timeline_chunks, ensure_ascii=False)}\n"
            f"抽样观察: {json.dumps(compact_records, ensure_ascii=False)}"
        )

    def compact_fallback(self, fallback: dict[str, Any]) -> dict[str, Any]:
        categories = fallback.get("categories") or {}
        compact_categories = {
            key: [
                truncate(str(item), 180)
                for item in (categories.get(key) or [])[:5]
            ]
            for key in (
                "daily",
                "complaint",
                "bug",
                "economy",
                "moderation",
                "suggestion",
                "cross_server",
            )
        }
        compact_issues = []
        for issue in (fallback.get("issues") or [])[:8]:
            item = dict(issue)
            samples = item.get("evidence_samples") or []
            item["evidence_samples"] = [
                compact_evidence_sample(str(sample)) for sample in samples[:2]
            ]
            compact_issues.append(item)
        return {
            "summary": truncate(str(fallback.get("summary") or ""), 300),
            "time_window": fallback.get("time_window"),
            "servers": (fallback.get("servers") or [])[:20],
            "chat_count": fallback.get("chat_count", 0),
            "chat_players": (fallback.get("chat_players") or [])[
                :MAX_DISPLAY_PLAYERS
            ],
            "dialogue_findings": [
                truncate(str(item), 220)
                for item in (fallback.get("dialogue_findings") or [])[:8]
            ],
            "categories": compact_categories,
            "issues": compact_issues,
            "ops_notes": [
                truncate(str(note), 180)
                for note in (fallback.get("ops_notes") or [])[:8]
            ],
        }

    def timeline_chunks(
        self,
        records: list[ObservationRecord],
    ) -> list[dict[str, Any]]:
        if not records:
            return []

        chunk_count = min(
            8,
            max(1, self.config.report.max_ai_records // 20),
            len(records),
        )
        chunk_size = max(1, math.ceil(len(records) / chunk_count))
        chunks: list[dict[str, Any]] = []
        for index in range(0, len(records), chunk_size):
            group = records[index : index + chunk_size]
            if not group:
                continue
            kinds = Counter(record.kind for record in group)
            tags = Counter(tag for record in group for tag in record.tags if tag)
            players = player_name_list(group)
            samples = [
                truncate(record.evidence_text(), 160)
                for record in even_sample(group, min(4, len(group)))
            ]
            chunks.append(
                {
                    "start_ts": group[0].timestamp,
                    "end_ts": group[-1].timestamp,
                    "count": len(group),
                    "chat_count": sum(1 for record in group if record.kind == "CHAT"),
                    "kinds": dict(kinds.most_common(8)),
                    "players": players[:MAX_DISPLAY_PLAYERS],
                    "players_text": format_players(players),
                    "top_tags": [tag for tag, _ in tags.most_common(8)],
                    "samples": samples,
                }
            )
        return chunks

    def sample_for_ai(
        self,
        records: list[ObservationRecord],
        fallback: dict[str, Any] | None = None,
    ) -> list[ObservationRecord]:
        return sample_records_for_ai(
            records,
            self.config.report.max_ai_records,
            fallback,
        )

    def compact_record(self, record: ObservationRecord) -> dict[str, Any]:
        return {
            "kind": record.kind,
            "server": record.server_id,
            "backend": record.backend_server,
            "player": record.player_name,
            "content": truncate(
                record.content,
                self.config.report.max_ai_content_length,
            ),
            "context": record.context,
            "metrics": record.metrics,
            "timestamp": record.timestamp,
        }

    @staticmethod
    def drop_chunk_samples(chunks: list[dict[str, Any]]) -> bool:
        changed = False
        for chunk in chunks:
            samples = chunk.get("samples") or []
            if len(samples) > 1:
                chunk["samples"] = samples[:1]
                changed = True
            elif samples:
                chunk["samples"] = []
                changed = True
        return changed


def truncate(value: str, max_length: int) -> str:
    if max_length <= 0:
        return ""
    if len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return value[: max_length - 3] + "..."


def compact_evidence_sample(sample: str) -> str:
    if "\n" not in sample or not sample.startswith("上下文 "):
        return truncate(sample, 220)

    lines = [line for line in sample.splitlines() if line.strip()]
    if not lines:
        return ""
    target_index = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith(">")),
        min(1, len(lines) - 1),
    )
    selected_indexes = {0, target_index}
    radius = 1
    while len(selected_indexes) < min(MAX_CONTEXT_LINES, len(lines)):
        before = target_index - radius
        after = target_index + radius
        if before > 0:
            selected_indexes.add(before)
        if len(selected_indexes) >= min(MAX_CONTEXT_LINES, len(lines)):
            break
        if after < len(lines):
            selected_indexes.add(after)
        if before <= 0 and after >= len(lines):
            break
        radius += 1

    compact_lines = [
        truncate(lines[index], MAX_CONTEXT_LINE_CHARS)
        for index in sorted(selected_indexes)
    ]
    return truncate("\n".join(compact_lines), MAX_EVIDENCE_SAMPLE_CHARS)
