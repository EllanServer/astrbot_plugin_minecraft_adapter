"""SERVER_LOG priority scoring (Rust 可选)。

当 ``mine_sentinel_rs`` 平台 wheel 可导入时，priority scoring 委托给
原生扩展；缺失时自动降级为纯 Python 实现，行为完全等价。与 codec.py
一致，避免插件因缺少 Rust wheel 而无法加载。
"""

from __future__ import annotations

try:
    from mine_sentinel_rs import (
        observation_priority_score as _rs_observation_priority_score,
    )

    _HAS_RUST = True
except ImportError:  # pragma: no cover - 纯 Python 降级路径
    _HAS_RUST = False

from .models import ObservationRecord

# 与 Rust 侧 server_log_priority 的 marker 列表保持一致；
# 任一命中即给 +4.0 分（仅首个命中，break）。
_PRIORITY_MARKERS = (
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


def _python_priority_score(record: ObservationRecord) -> float:
    """纯 Python fallback：镜像 Rust observation_priority_score 的逻辑。"""
    kind = getattr(record, "kind", "") or ""
    if kind != "SERVER_LOG":
        return 0.0
    content = (getattr(record, "content", "") or "").lower()
    tags = getattr(record, "tags", []) or []
    text = content + " " + " ".join(str(t).lower() for t in tags) + " "
    score = 1.0
    for marker in _PRIORITY_MARKERS:
        if marker in text:
            score += 4.0
            break
    return score


def observation_priority_score(record: ObservationRecord) -> float:
    """Score runtime log records that should survive bounded report sampling."""
    if _HAS_RUST:
        return _rs_observation_priority_score(record)
    return _python_priority_score(record)


__all__ = ["observation_priority_score"]
