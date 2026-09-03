"""Web 服务核心：NL -> DSL -> SQL -> 结果 -> 解释 -> 图表 的完整链路。

纯标准库 + 既有业务模块，不引入任何 Web 框架，保持"零外部依赖"。
同时负责审计埋点（P0）：把每次问答的 request_id / session_id / user / prompt /
检索上下文 / DSL / 最终 SQL / 耗时 / 返回行数 / 错误 落盘，并输出结构化日志。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from agent.pipeline import rewrite_dsl, run_pipeline
from agent.router import Action, route_query
from audit.logging import get_logger, set_request_context
from audit.record import AuditRecord
from audit.store import AuditStore
from compiler.sql_compiler import CompileError, compile_sql
from config import settings
from exec.guards import SqlExecutionError, execute_sql
from present.explain import explain
from present.viz import viz_config
from security.guard import apply_policy
from semantic.catalog import COLUMNS
from semantic.dsl_schema import QueryDSL, RatioMetric, WindowMetric

logger = get_logger("web.service")

_default_store: AuditStore | None = None
_store_lock = threading.Lock()


def _default_audit_store() -> AuditStore | None:
    """进程内复用的默认审计存储（按配置启用，双写 JSONL + DuckDB）。"""
    global _default_store
    if not settings.AUDIT_ENABLED:
        return None
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = AuditStore(
                    jsonl_path=settings.AUDIT_LOG_PATH,
                    db_path=settings.AUDIT_DB_PATH,
                )
    return _default_store


def _json_safe(value: Any) -> Any:
    """把 DuckDB 返回值转成 JSON 可序列化类型（datetime/date/Decimal）。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _referenced_fields(dsl: QueryDSL) -> set[str]:
    """收集 DSL 引用的全部逻辑字段（指标 + 维度 + 过滤）。"""
    fields: set[str] = set()
    for m in dsl.metrics:
        if isinstance(m, RatioMetric):
            fields.add(m.numerator.field)
            fields.add(m.denominator.field)
        elif isinstance(m, WindowMetric):
            fields.add(m.base.field)
        else:
            fields.add(m.field)
    for d in dsl.dimensions:
        fields.add(d.field)
    for f in dsl.filters:
        fields.add(f.field)
    return fields


def _retrieval_context(dsl: QueryDSL) -> dict[str, Any]:
    """从 DSL 派生"检索上下文"：本次查询命中的语义目录字段与物理表。"""
    fields = _referenced_fields(dsl)
    tables = sorted({COLUMNS[f].table for f in fields if f in COLUMNS})
    return {"fields": sorted(fields), "tables": tables}


def _execute_with_self_heal(
    query: str,
    dsl: QueryDSL,
    conn: duckdb.DuckDBPyConnection,
    *,
    principal: str | None = None,
) -> tuple[QueryDSL, str, Any, int]:
    """编译 + 受控执行 + SQL 自愈重写循环（P0/P1）。

    编译（CompileError）或执行（SqlExecutionError：精确引擎报错 / 查询超时 /
    扫描行数熔断 / LIMIT 硬上限）失败时，把精确报错喂回 LLM 重写 DSL 并重试
    （至少 1 次，受 settings.SQL_SELF_HEAL_MAX_RETRIES 约束）。重写后的 DSL
    仍会重新经过安全守卫（防权限逃逸）。未配置 LLM 或重写失败时透传原始报错，
    绝不静默猜测。返回 (final_dsl, final_sql, ExecutionResult, rewrites)。
    """
    max_rewrites = settings.SQL_SELF_HEAL_MAX_RETRIES
    current_dsl = dsl
    rewrites = 0
    while True:
        try:
            sql = compile_sql(current_dsl)
            exec_result = execute_sql(
                conn,
                sql,
                statement_timeout_ms=settings.QUERY_TIMEOUT_MS,
                max_scan_rows=settings.MAX_SCAN_ROWS,
                max_result_rows=settings.MAX_RESULT_ROWS,
            )
            return current_dsl, sql, exec_result, rewrites
        except (CompileError, SqlExecutionError) as exc:
            if rewrites >= max_rewrites:
                raise
            try:
                rewritten = rewrite_dsl(
                    query,
                    current_dsl,
                    f"{type(exc).__name__}: {exc}",
                    attempts=1,
                )
                current_dsl = apply_policy(rewritten, principal)
                rewrites += 1
            except Exception:
                # 自愈失败（无 LLM / LLM 拒绝 / 安全守卫拒绝）-> 透传原始报错
                raise exc from None


def run_query(
    query: str,
    principal: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    user: str | None = None,
    retrieval_context: dict[str, Any] | None = None,
    audit_store: AuditStore | None = None,
) -> dict[str, Any]:
    """执行完整链路，返回可直接交给前端渲染的字典；失败时写入 error 字段。

    conn 传入时复用该连接（测试用内存库），否则打开本地 DuckDB 文件只读。

    每次调用都会：
    1. 注入结构化日志上下文（request_id 贯穿），生成/复用 request_id；
    2. 落一条审计记录（JSONL 对象存储 + DuckDB 审计表），记录耗时/行数/错误等。
    """
    # 注入结构化日志上下文；request_id 由调用方传入或在此生成
    rid = set_request_context(request_id=request_id, session_id=session_id, user=user)
    logger.info("query_start", extra={"event": "query_start"})

    started = time.perf_counter()
    result: dict[str, Any] = {
        "query": query,
        "principal": principal,
        "request_id": rid,
    }

    dsl: QueryDSL | None = None
    sql: str | None = None
    row_count: int | None = None
    scan_rows: int | None = None
    rewrites: int | None = None
    error: str | None = None

    ctx_override: dict[str, Any] | None = None
    try:
        route = route_query(query)
        result["intent"] = route.intent.value
        result["action"] = route.action.value
        result["message"] = route.message

        if route.action == Action.CHITCHAT:
            error = route.message
            result["error"] = error
        elif route.action == Action.CLARIFY:
            result["clarifications"] = [c.to_dict() for c in route.clarifications]
            error = route.message
            result["error"] = error
        elif route.action == Action.RAG:
            result["documents"] = [d.to_dict() for d in route.documents]
            ctx_override = {"intent": "rag", "documents": [d.key for d in route.documents]}
        else:
            dsl = run_pipeline(query, principal)
            result["dsl"] = dsl.model_dump(mode="json")

            own_conn = conn is None
            if own_conn:
                conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
            try:
                final_dsl, sql, exec_result, rewrites = _execute_with_self_heal(
                    query, dsl, conn, principal=principal
                )
            finally:
                if own_conn:
                    conn.close()

            dsl = final_dsl
            result["dsl"] = dsl.model_dump(mode="json")
            result["sql"] = sql
            result["rewrites"] = rewrites
            result["scan_rows"] = exec_result.scan_rows
            columns = list(exec_result.columns)
            rows = [[_json_safe(v) for v in row] for row in exec_result.rows]
            result["columns"] = columns
            result["rows"] = rows
            row_count = len(rows)
            scan_rows = exec_result.scan_rows
            result["explanation"] = explain(dsl)
            result["viz"] = viz_config(dsl, columns, rows)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result["error"] = error

    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
    if ctx_override is not None:
        ctx = ctx_override
    elif retrieval_context is not None:
        ctx = retrieval_context
    else:
        ctx = _retrieval_context(dsl) if dsl is not None else None

    record = AuditRecord(
        request_id=rid,
        session_id=session_id,
        user=user or principal,
        prompt=query,
        retrieval_context=ctx,
        dsl=dsl.model_dump(mode="json") if dsl is not None else None,
        sql=sql,
        latency_ms=latency_ms,
        row_count=row_count,
        scan_rows=scan_rows,
        rewrites=rewrites,
        error=error,
    )

    store = audit_store if audit_store is not None else _default_audit_store()
    if store is not None:
        try:
            store.write(record)
        except Exception:  # 审计失败绝不影响主链路
            logger.exception("audit_write_failed", extra={"event": "audit_write_failed"})

    if error:
        logger.warning(
            "query_end_error",
            extra={"event": "query_end", "status": "error", "latency_ms": latency_ms},
        )
    else:
        logger.info(
            "query_end_ok",
            extra={
                "event": "query_end",
                "status": "ok",
                "latency_ms": latency_ms,
                "row_count": row_count,
            },
        )
    return result


def ensure_db() -> None:
    """确保本地 DuckDB 数仓文件存在，缺失时幂等重建。"""
    if settings.DB_PATH.exists():
        return
    from mock.init_duckdb import main as init_db

    init_db()
