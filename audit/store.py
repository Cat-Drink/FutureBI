"""审计持久化：JSONL 对象存储 + DuckDB 审计表。

两种 sink 可同时启用，任一写入失败都只记录日志、不影响主查询链路：
- JSONL 对象存储：逐行追加，人类可读、跨进程可追写；
- DuckDB 审计表 audit_log：可 SQL 查询、便于聚合分析。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import duckdb

from audit.record import AuditRecord

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    request_id        VARCHAR,
    session_id        VARCHAR,
    user_name         VARCHAR,
    prompt            VARCHAR,
    retrieval_context VARCHAR,
    dsl               VARCHAR,
    sql               VARCHAR,
    latency_ms        DOUBLE,
    row_count         BIGINT,
    scan_rows         BIGINT,
    rewrites          BIGINT,
    error             VARCHAR,
    created_at        VARCHAR
)
"""

_INSERT_SQL = """
INSERT INTO audit_log
    (request_id, session_id, user_name, prompt, retrieval_context,
     dsl, sql, latency_ms, row_count, scan_rows, rewrites, error, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _json_dump(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


class AuditStore:
    """线程安全的审计写入器（JSONL + DuckDB 审计表）。"""

    def __init__(self, jsonl_path: Path | None = None, db_path: Path | None = None) -> None:
        self.jsonl_path = jsonl_path
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None

    def write(self, record: AuditRecord) -> None:
        with self._lock:
            if self.jsonl_path is not None:
                self._append_jsonl(record)
            if self.db_path is not None:
                self._insert_db(record)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> AuditStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def _append_jsonl(self, record: AuditRecord) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _insert_db(self, record: AuditRecord) -> None:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
            self._conn.execute(_AUDIT_DDL)
        self._conn.execute(
            _INSERT_SQL,
            [
                record.request_id,
                record.session_id,
                record.user,
                record.prompt,
                _json_dump(record.retrieval_context),
                _json_dump(record.dsl),
                record.sql,
                record.latency_ms,
                record.row_count,
                record.scan_rows,
                record.rewrites,
                record.error,
                record.created_at,
            ],
        )
