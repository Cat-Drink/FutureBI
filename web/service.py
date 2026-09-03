"""Web 服务核心：NL -> DSL -> SQL -> 结果 -> 解释 -> 图表 的完整链路。

纯标准库 + 既有业务模块，不引入任何 Web 框架，保持"零外部依赖"。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from agent.pipeline import run_pipeline
from compiler.sql_compiler import compile_sql
from config import settings
from present.explain import explain
from present.viz import viz_config


def _json_safe(value: Any) -> Any:
    """把 DuckDB 返回值转成 JSON 可序列化类型（datetime/date/Decimal）。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def run_query(
    query: str,
    principal: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, Any]:
    """执行完整链路，返回可直接交给前端渲染的字典；失败时写入 error 字段。

    conn 传入时复用该连接（测试用内存库），否则打开本地 DuckDB 文件只读。
    """
    result: dict[str, Any] = {"query": query, "principal": principal}
    try:
        dsl = run_pipeline(query, principal)
        result["dsl"] = dsl.model_dump(mode="json")

        sql = compile_sql(dsl)
        result["sql"] = sql

        own_conn = conn is None
        if own_conn:
            conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
        try:
            cur = conn.execute(sql)
            columns = tuple(d[0] for d in cur.description)
            rows = [[_json_safe(v) for v in row] for row in cur.fetchall()]
        finally:
            if own_conn:
                conn.close()

        result["columns"] = list(columns)
        result["rows"] = rows
        result["explanation"] = explain(dsl)
        result["viz"] = viz_config(dsl, columns, rows)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def ensure_db() -> None:
    """确保本地 DuckDB 数仓文件存在，缺失时幂等重建。"""
    if settings.DB_PATH.exists():
        return
    from mock.init_duckdb import main as init_db

    init_db()
