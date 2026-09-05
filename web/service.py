"""Web 服务核心：NL -> DSL -> SQL -> 结果 -> 解释 -> 图表 的完整链路。

纯标准库 + 既有业务模块，不引入任何 Web 框架，保持"零外部依赖"。
同时负责审计埋点（P0）：把每次问答的 request_id / session_id / user / prompt /
检索上下文 / DSL / 最终 SQL / 耗时 / 返回行数 / 错误 落盘，并输出结构化日志。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import duckdb

from agent.errors import PipelineError
from agent.memory import (
    SessionState,
    append_message,
    default_session_store,
    derive_active_entities,
    resolve_context,
)
from agent.pipeline import rewrite_dsl
from agent.router import Action, route_query
from agent.slotfill import ClarifyContext, attempt_fill, default_slot_store, pending_kinds
from agent.tool_agent import AgentResult, default_tool_agent
from audit.logging import get_logger, set_request_context
from audit.metrics import default_registry
from audit.record import AuditRecord
from audit.store import AuditStore
from compiler.sql_compiler import CompileError
from config import settings
from exec.guards import (
    MaxRowsScannedExceeded,
    QueryTimeoutError,
    ResultLimitExceeded,
    SqlExecutionError,
    UnsafeSqlError,
    execute_sql,
)
from exec.pool import ReadOnlyConnectionPool
from security.errors import SecurityError
from semantic import catalog
from semantic.dsl_schema import QueryDSL, RatioMetric, WindowMetric

logger = get_logger("web.service")


def _friendly_error(exc: Exception) -> str:
    """将内部异常映射为面向业务用户的安全提示。"""
    if isinstance(exc, QueryTimeoutError):
        return "查询超时，请缩小时间范围后重试"
    if isinstance(exc, MaxRowsScannedExceeded):
        return "查询范围过大，已自动中止，请缩小时间范围后重试"
    if isinstance(exc, ResultLimitExceeded):
        return "查询结果过多，请缩小查询范围后重试"
    if isinstance(exc, SecurityError):
        return "您无权查看该数据，请联系管理员确认权限"
    if isinstance(exc, PipelineError):
        return "暂时无法理解该问题，请补充时间范围或使用已定义的指标"
    if isinstance(exc, UnsafeSqlError):
        return "查询未通过安全校验，请调整问题后重试"
    if isinstance(exc, CompileError):
        return "查询条件无法编译，请调整指标或过滤条件后重试"
    if isinstance(exc, SqlExecutionError):
        return "查询执行出错，请调整条件后重试"
    return "系统繁忙，请稍后重试"


# 工具失败按异常类型名映射为友好提示（与 _friendly_error 同源，供工具层错误使用）
_FRIENDLY_BY_ERROR_CLASS = {
    "QueryTimeoutError": "查询超时，请缩小时间范围后重试",
    "MaxRowsScannedExceeded": "查询范围过大，已自动中止，请缩小时间范围后重试",
    "ResultLimitExceeded": "查询结果过多，请缩小查询范围后重试",
    "SecurityError": "您无权查看该数据，请联系管理员确认权限",
    "PipelineError": "暂时无法理解该问题，请补充时间范围或使用已定义的指标",
    "UnsafeSqlError": "查询未通过安全校验，请调整问题后重试",
    "CompileError": "查询条件无法编译，请调整指标或过滤条件后重试",
    "SqlExecutionError": "查询执行出错，请调整条件后重试",
}

_CIRCUIT_BY_ERROR_CLASS = {
    "QueryTimeoutError": "query_timeout",
    "MaxRowsScannedExceeded": "scan_rows",
    "ResultLimitExceeded": "result_limit",
    "UnsafeSqlError": "unsafe_sql",
}


def _friendly_by_class(error_type: str | None) -> str | None:
    if error_type is None:
        return None
    return _FRIENDLY_BY_ERROR_CLASS.get(error_type)


_default_store: AuditStore | None = None
_store_lock = threading.Lock()

# P0-6 并发闸：全局信号量限制同时执行的查询数（超配额排队等待，配合连接池）
_query_gate = threading.BoundedSemaphore(settings.MAX_CONCURRENT_QUERIES)

_default_pool: ReadOnlyConnectionPool | None = None
_pool_lock = threading.Lock()


def _default_db_pool() -> ReadOnlyConnectionPool:
    """进程内复用的只读连接池（惰性初始化，容量取 settings.DB_POOL_SIZE）。"""
    global _default_pool
    if _default_pool is None:
        with _pool_lock:
            if _default_pool is None:
                _default_pool = ReadOnlyConnectionPool(settings.DB_PATH, settings.DB_POOL_SIZE)
    return _default_pool


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
    tables = sorted({catalog.COLUMNS[f].table for f in fields if f in catalog.COLUMNS})
    return {"fields": sorted(fields), "tables": tables}


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
        "session_id": session_id,
    }

    dsl: QueryDSL | None = None
    sql: str | None = None
    row_count: int | None = None
    scan_rows: int | None = None
    rewrites: int | None = None
    error: str | None = None
    circuit_breaker: str | None = None

    ctx_override: dict[str, Any] | None = None
    effective_query = query
    # 会话上下文记忆（Session Memory）：按 (session_id, user_id) 强绑定取状态；
    # 跨用户 / 过期一律返回 None（拒绝继承，Session Bleeding 防护）。
    memory_store = default_session_store() if session_id else None
    owner = user or principal or "anonymous"
    state = memory_store.get(session_id, owner) if memory_store is not None else None
    base_dsl: Any = None
    context_summary: str | None = None
    try:
        # P0-5 澄清槽位回填：命中上一轮挂起上下文且答案可填槽时，合并回原问题
        slot_store = default_slot_store() if session_id else None
        pending = slot_store.get(session_id) if slot_store is not None else None
        if pending is not None and pending_kinds(pending):
            merged = attempt_fill(pending, query)
            if merged is not None:
                effective_query = merged
                result["clarify_filled"] = True
                result["resolved_query"] = effective_query
                result["filled_from"] = pending.original_query
                slot_store.clear(session_id)

        route = route_query(effective_query, principal)
        result["intent"] = route.intent.value
        result["action"] = route.action.value
        result["message"] = route.message

        if route.action == Action.CHITCHAT:
            if slot_store is not None:
                slot_store.clear(session_id)
            # 话题切换：闲聊轮显式清理旧 DSL 依赖，避免脏上下文干扰后续查询
            if memory_store is not None and state is not None:
                state.last_dsl = None
                append_message(state, "user", effective_query)
                memory_store.update(session_id, owner, state)
            error = route.message
            result["error"] = error
            result["steps"] = []
        elif route.action == Action.CLARIFY:
            # 保存澄清上下文，供用户短语回答时回填槽位
            if slot_store is not None:
                slot_store.set(
                    session_id,
                    ClarifyContext(
                        original_query=effective_query,
                        pending=tuple(c.kind for c in route.clarifications),
                    ),
                )
            # 澄清轮保留上轮有效 DSL（用户补口径后可能回到原查询语境）
            if memory_store is not None and state is not None:
                append_message(state, "user", effective_query)
                memory_store.update(session_id, owner, state)
            result["clarifications"] = [c.to_dict() for c in route.clarifications]
            result["steps"] = []
            error = route.message
            result["error"] = error
        elif route.action == Action.RAG:
            # 口径文档检索：经 explain_glossary_tool 执行，记录调度轨迹（不触达 SQL 引擎）
            if slot_store is not None:
                slot_store.clear(session_id)
            agent_result = default_tool_agent().run(effective_query, principal, request_id=rid)
            result["steps"] = [s.to_dict() for s in agent_result.steps]
            result["answer"] = agent_result.answer
            result["documents"] = agent_result.documents

            # P1-5: LLM clarify 路径需要回填槽位
            if agent_result.clarifications and slot_store is not None:
                slot_store.set(
                    session_id,
                    ClarifyContext(
                        original_query=effective_query,
                        pending=tuple(c.get("kind") for c in agent_result.clarifications),
                    ),
                )

            result["message"] = (
                route.message
                if agent_result.documents
                else "未检索到相关口径文档，请换一种表述或补充指标口径。"
            )
            # 话题切换：口径检索轮显式清理旧 DSL 依赖
            if memory_store is not None and state is not None:
                state.last_dsl = None
                append_message(state, "user", effective_query)
                append_message(state, "assistant", agent_result.answer or "")
                memory_store.update(session_id, owner, state)
            ctx_override = {
                "intent": "rag",
                "documents": [d["key"] for d in agent_result.documents],
            }
        else:
            # TEXT2SQL：Multi-Tool Agent 调度（Plan & Select -> Execute & Guard -> Reflect）
            if slot_store is not None:
                slot_store.clear(session_id)
            agent = default_tool_agent()

            # 会话上下文继承与消解（Contextual Merging）：
            # - 省略指代 / 下钻 -> 注入基于上轮 DSL 的合并结果（base_dsl）；
            # - 话题切换 -> 显式清理旧 DSL 依赖（last_dsl 置空）。
            if memory_store is not None and state is not None:
                resolution = resolve_context(effective_query, state.last_dsl, principal)
                if resolution.mode in ("inherit", "drilldown"):
                    base_dsl = resolution.dsl
                    context_summary = resolution.summary or None
                elif resolution.mode == "fresh" and resolution.reason == "topic_switch":
                    state.last_dsl = None
                    context_summary = resolution.summary or None

            # P0-6：未显式注入连接时，从只读连接池取用（用完归还），
            # 并用全局信号量限制并发查询数（超配额排队，避免打满单机实例）。
            own_conn = conn is None
            pool = None
            if own_conn:
                pool = _default_db_pool()
                conn = pool.acquire()
            try:
                with _query_gate:
                    agent_result: AgentResult = agent.run(
                        effective_query,
                        principal,
                        conn=conn,
                        executor=execute_sql,  # 模块级绑定：测试桩在调用期生效
                        rewriter=rewrite_dsl,
                        request_id=rid,
                        base_dsl=base_dsl,  # 会话上下文继承注入（None 时行为不变）
                    )
            finally:
                if own_conn and pool is not None:
                    pool.release(conn)

            result["steps"] = [s.to_dict() for s in agent_result.steps]
            result["answer"] = agent_result.answer
            result["chart_spec"] = agent_result.chart_spec
            result["download_urls"] = agent_result.download_urls
            result["degraded"] = agent_result.degraded
            result["mode"] = "degraded" if agent_result.degraded else "normal"
            result["rewrites"] = agent_result.rewrites
            result["scan_rows"] = agent_result.scan_rows

            if agent_result.error:
                error = _friendly_by_class(agent_result.error_type) or "系统繁忙，请稍后重试"
                result["error"] = error
                result["error_detail"] = agent_result.error
                circuit_breaker = _CIRCUIT_BY_ERROR_CLASS.get(agent_result.error_type)
                # 自愈彻底失败不污染上轮有效状态：仅追加用户消息，last_dsl 保持不动
                if memory_store is not None and state is not None:
                    append_message(state, "user", effective_query)
                    memory_store.update(session_id, owner, state)
            else:
                dsl = agent_result.dsl
                sql = agent_result.sql
                columns = agent_result.columns or []
                rows = agent_result.rows or []
                rewrites = agent_result.rewrites
                scan_rows = agent_result.scan_rows
                row_count = len(rows)
                result["dsl"] = dsl.model_dump(mode="json") if dsl is not None else None
                result["sql"] = sql
                result["columns"] = columns
                result["rows"] = rows
                result["explanation"] = agent_result.explanation
                result["viz"] = agent_result.viz
                # 执行闭环写回：查询成功并通过安全校验后，更新 last_dsl / active_entities /
                # 滚动历史（供下一轮省略指代 / 下钻 / 话题切换判定）
                if memory_store is not None and dsl is not None:
                    if state is None:
                        state = SessionState(session_id=session_id, user_id=owner)
                    state.last_dsl = dsl
                    state.active_entities = derive_active_entities(dsl)
                    append_message(state, "user", effective_query)
                    append_message(state, "assistant", agent_result.answer or "", dsl=dsl)
                    memory_store.update(session_id, owner, state)
    except Exception as exc:
        error = _friendly_error(exc)
        result["error"] = error
        result["error_detail"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, QueryTimeoutError):
            circuit_breaker = "query_timeout"
        elif isinstance(exc, MaxRowsScannedExceeded):
            circuit_breaker = "scan_rows"
        elif isinstance(exc, ResultLimitExceeded):
            circuit_breaker = "result_limit"
        elif isinstance(exc, UnsafeSqlError):
            circuit_breaker = "unsafe_sql"

    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
    if ctx_override is not None:
        ctx = ctx_override
    elif retrieval_context is not None:
        ctx = retrieval_context
    else:
        ctx = _retrieval_context(dsl) if dsl is not None else None

    # 澄清槽位回填（P0-5）：把合并来源写进审计检索上下文，保证澄清对话可追溯
    if result.get("clarify_filled"):
        ctx = dict(ctx or {})
        ctx["clarify_filled"] = True
        ctx["filled_from"] = result.get("filled_from")

    # 可观测性打点（P0 / §4 项5）：QPS、P50/P95、意图分布、自愈/熔断/降级
    try:
        default_registry().record_query(
            intent=str(result.get("intent", "unknown")),
            action=str(result.get("action", "unknown")),
            latency_ms=latency_ms,
            error=error,
            degraded=bool(result.get("degraded")),
            rewrites=rewrites or 0,
            clarify_filled=bool(result.get("clarify_filled")),
            circuit_breaker=circuit_breaker,
        )
    except Exception:  # 指标打点失败绝不影响主链路
        logger.exception("metrics_record_failed", extra={"event": "metrics_record_failed"})

    record = AuditRecord(
        request_id=rid,
        session_id=session_id,
        user=user or principal,
        principal=principal,
        prompt=query,
        retrieval_context=ctx,
        dsl=dsl.model_dump(mode="json") if dsl is not None else None,
        sql=sql,
        latency_ms=latency_ms,
        row_count=row_count,
        scan_rows=scan_rows,
        rewrites=rewrites,
        error=error,
        steps=result.get("steps"),
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
    # 响应契约：透传会话继承/重写说明（前端感知多轮上下文复用）
    if context_summary:
        result["context_summary"] = context_summary
    return result


def ensure_db() -> None:
    """确保本地 DuckDB 数仓文件存在，缺失时幂等重建；随后重建语义目录（P0-2）
    与权限策略（P0-3），使"新增表/字段/权限"全部走配置而非改代码。"""
    if not settings.DB_PATH.exists():
        from mock.init_duckdb import main as init_db

        init_db()
    from security.policy_loader import refresh_policies
    from semantic.catalog_loader import refresh_catalog

    refresh_catalog(db_path=settings.DB_PATH)
    refresh_policies()
