"""审计埋点与结构化日志（P0）。

- AuditRecord：一次查询的审计快照；
- AuditStore：JSONL 对象存储 + DuckDB 审计表双写；
- logging：JSON 结构化日志 + request_id/session_id/user 上下文贯穿。
"""

from audit.logging import (
    JsonFormatter,
    get_logger,
    get_request_id,
    set_request_context,
    setup_logging,
)
from audit.record import AuditRecord
from audit.store import AuditStore

__all__ = [
    "AuditRecord",
    "AuditStore",
    "JsonFormatter",
    "get_logger",
    "get_request_id",
    "set_request_context",
    "setup_logging",
]
