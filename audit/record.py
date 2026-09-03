"""审计记录模型：一次 NL -> SQL 查询的完整可追溯快照。

审计把每次问答的上下文与产物落盘，供合规审计、问题回溯与质量分析使用。
字段与目标（P0）一一对应：session_id / user / prompt / 检索上下文 / DSL /
最终 SQL / 耗时 / 返回行数 / 扫描行数 / 自愈重写次数 / 错误。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AuditRecord:
    """单次查询的审计快照。"""

    request_id: str
    prompt: str
    session_id: str | None = None
    user: str | None = None
    retrieval_context: dict[str, Any] | None = None
    dsl: dict[str, Any] | None = None
    sql: str | None = None
    latency_ms: float | None = None
    row_count: int | None = None
    scan_rows: int | None = None
    rewrites: int | None = None
    error: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
