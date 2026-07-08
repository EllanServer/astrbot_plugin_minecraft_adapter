"""Prompt construction for AI-assisted MineSentinel reports."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from ..models import MineSentinelConfig, ObservationRecord
from .common import MAX_DISPLAY_PLAYERS, format_players
from .sampling import even_sample, sample_records_for_ai


MAX_EVIDENCE_SAMPLE_CHARS = 520
MAX_CONTEXT_LINE_CHARS = 180
MAX_CONTEXT_LINES = 5
JSON_SEPARATORS = (",", ":")
ISSUE_PROMPT_FIELDS = (
    "category",
    "tag",
    "source_tag",
    "incident_index",
    "severity",
    "title",
    "players",
    "mentioned_players",
    "affected_servers",
    "affected_backends",
    "affected_locations",
    "dialogue_terms",
    "metric_context_text",
    "evidence_count",
    "signal_count",
    "unique_players",
    "first_seen_ts",
    "last_seen_ts",
    "suggested_action",
)


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
        fallback_json = compact_json(self.compact_fallback(fallback))
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
        records_json = compact_json(records)
        chunks_json = compact_json(chunks)

        while True:
            prompt = self.prompt_text_from_json_v2(
                window_minutes,
                fallback_json,
                chunks_json,
                records_json,
            )
            if len(prompt) <= max_chars:
                return prompt
            if records:
                records = even_sample(records, max(0, len(records) * 3 // 4))
                records_json = compact_json(records)
                continue
            if self.drop_chunk_samples(chunks):
                chunks_json = compact_json(chunks)
                continue
            if len(chunks) > 1:
                chunks = even_sample(chunks, max(1, len(chunks) // 2))
                chunks_json = compact_json(chunks)
                continue
            return prompt[:max_chars]

    @staticmethod
    def prompt_text(
        window_minutes: int,
        fallback_json: str,
        timeline_chunks: list[dict[str, Any]],
        compact_records: list[dict[str, Any]],
    ) -> str:
        return AIReportPromptBuilder.prompt_text_from_json_v2(
            window_minutes,
            fallback_json,
            compact_json(timeline_chunks),
            compact_json(compact_records),
        )

    @staticmethod
    def prompt_text_from_json_v2(
        window_minutes: int,
        fallback_json: str,
        timeline_json: str,
        records_json: str,
    ) -> str:
        return (
            "You are MineSentinel report-polishing assistant, using JSON facts only (no raw logs).\n"
            "Return exactly one JSON object and do not add markdown, code fences, comments, or prose.\n"
            "Preserve and polish these top-level fields: summary, time_window, servers, chat_count, "
            "chat_players, dialogue_findings, categories, issues, ops_notes.\n"
            "Issue fields (if available) include: category, tag, source_tag, incident_index, severity, title, "
            "players, mentioned_players, affected_servers, affected_backends, affected_locations, dialogue_terms, "
            "metric_context_text, evidence_count, signal_count, unique_players, first_seen_ts, last_seen_ts, "
            "suggested_action.\n"
            "Keep semantics stable and only add concise wording improvements.\n"
            f"\ntime_window_minutes: {window_minutes}\n"
            f"fallback_json: {fallback_json}\n"
            f"timeline_chunks: {timeline_json}\n"
            f"compact_records: {records_json}"
        )

    @staticmethod
    def prompt_text_from_json(
        window_minutes: int,
        fallback_json: str,
        timeline_json: str,
        records_json: str,
    ) -> str:
        return AIReportPromptBuilder.prompt_text_from_json_v2(
            window_minutes,
            fallback_json,
            timeline_json,
            records_json,
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
            if isinstance(issue, dict):
                compact_issues.append(compact_issue(issue))
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
            end = min(len(records), index + chunk_size)
            if index >= end:
                continue
            chunks.append(self._timeline_chunk(records, index, end))
        return chunks

    @staticmethod
    def _timeline_chunk(
        records: list[ObservationRecord],
        start: int,
        end: int,
    ) -> dict[str, Any]:
        kinds: Counter = Counter()
        tags: Counter = Counter()
        names: list[str] = []
        seen_names: set[str] = set()
        chat_count = 0
        for index in range(start, end):
            record = records[index]
            kinds[record.kind] += 1
            if record.kind == "CHAT":
                chat_count += 1
            tags.update(tag for tag in record.tags if tag)
            name = (record.player_name or record.identity or "").strip()
            if name and name not in seen_names:
                seen_names.add(name)
                names.append(name)

        players = sorted(names)
        samples = [
            truncate(records[index].evidence_text(), 160)
            for index in _sample_indexes(start, end, min(4, end - start))
        ]
        return {
            "start_ts": records[start].timestamp,
            "end_ts": records[end - 1].timestamp,
            "count": end - start,
            "chat_count": chat_count,
            "kinds": dict(kinds.most_common(8)),
            "players": players[:MAX_DISPLAY_PLAYERS],
            "players_text": format_players(players),
            "top_tags": [tag for tag, _ in tags.most_common(8)],
            "samples": samples,
        }

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


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=JSON_SEPARATORS)


def compact_issue(issue: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for field in ISSUE_PROMPT_FIELDS:
        if field not in issue:
            continue
        value = issue[field]
        if value in (None, "", [], {}):
            continue
        item[field] = value

    samples = issue.get("evidence_samples") or []
    if samples:
        item["evidence_samples"] = [
            compact_evidence_sample(str(sample)) for sample in samples[:2]
        ]
    return item


def _sample_indexes(start: int, end: int, max_items: int) -> list[int]:
    count = max(0, end - start)
    if count <= max_items:
        return list(range(start, end))
    if max_items <= 0:
        return []
    if max_items == 1:
        return [end - 1]
    step = (count - 1) / (max_items - 1)
    return [start + round(index * step) for index in range(max_items)]


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
