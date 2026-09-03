"""SQL 执行层资源治理单元测试（P0/P1）。

覆盖：
- execute_sql 正常执行（columns/rows/scan_rows/duration）；
- statement_timeout：超时中断并抛 QueryTimeoutError，连接可复用；
- 扫描行数上限：EXPLAIN ANALYZE 预检熔断，抛 MaxRowsScannedExceeded；
- LIMIT 硬上限：返回行数超限熔断，抛 ResultLimitExceeded；
- parse_scan_rows：解析 DuckDB 分析计划中的算子行数。
"""

from __future__ import annotations

import duckdb
import pytest

from exec.guards import (
    MaxRowsScannedExceeded,
    QueryTimeoutError,
    ResultLimitExceeded,
    SqlExecutionError,
    UnsafeSqlError,
    execute_sql,
    parse_scan_rows,
)


@pytest.fixture
def big_conn() -> duckdb.DuckDBPyConnection:
    """带大表的独立内存连接（用于超时/扫描熔断测试）。"""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE big AS SELECT range AS id, (range % 7) AS grp FROM range(200000)")
    yield c
    c.close()


def test_unsafe_sql_rejects_non_select(big_conn):
    """P0-2：DDL/DML 语句被只读白名单硬拦截。"""
    for sql in (
        "DROP TABLE big",
        "DELETE FROM big",
        "INSERT INTO big VALUES (1)",
        "CREATE TABLE x AS SELECT 1",
        "ATTACH 'x.duckdb'",
        "PRAGMA version",
    ):
        with pytest.raises(UnsafeSqlError):
            execute_sql(big_conn, sql)


def test_unsafe_sql_rejects_multi_statement(big_conn):
    """P0-2：堆叠多语句被拒绝（DuckDB 实测可执行 SELECT 1; SELECT 2）。"""
    with pytest.raises(UnsafeSqlError):
        execute_sql(big_conn, "SELECT 1; SELECT 2")


def test_unsafe_sql_allows_comment_and_with(big_conn):
    """注释先剥离再校验；WITH 与 SELECT 正常放行。"""
    result = execute_sql(big_conn, "-- 注释\nSELECT grp, sum(id) AS s FROM big GROUP BY grp")
    assert result.columns == ["grp", "s"]
    result2 = execute_sql(big_conn, "WITH t AS (SELECT 1 AS x) SELECT x FROM t")
    assert result2.columns == ["x"]


def test_unsafe_sql_literal_with_semicolon_is_allowed(big_conn):
    """字符串字面量内的分号不应被误判为多语句。"""
    result = execute_sql(big_conn, "SELECT 'a;b' AS v, grp FROM big WHERE grp = 1")
    assert result.columns[0] == "v"


def test_execute_sql_normal(big_conn):
    result = execute_sql(big_conn, "SELECT grp, sum(id) AS s FROM big GROUP BY grp ORDER BY grp")
    assert result.columns == ["grp", "s"]
    assert len(result.rows) == 7
    assert result.scan_rows == 0  # 未启用扫描预算时不做预检
    assert result.duration_ms >= 0


def test_scan_rows_cap_trips(big_conn):
    """扫描行数超过上限 -> 熔断拒绝执行。"""
    with pytest.raises(MaxRowsScannedExceeded) as excinfo:
        execute_sql(
            big_conn,
            "SELECT grp, sum(id) AS s FROM big GROUP BY grp",
            max_scan_rows=50000,
        )
    assert "200000" in str(excinfo.value)


def test_scan_rows_within_budget(big_conn):
    result = execute_sql(
        big_conn,
        "SELECT grp, sum(id) AS s FROM big GROUP BY grp",
        max_scan_rows=500000,
    )
    assert len(result.rows) == 7
    assert result.scan_rows == 200000


def test_result_limit_trips(big_conn):
    with pytest.raises(ResultLimitExceeded) as excinfo:
        execute_sql(
            big_conn,
            "SELECT grp, sum(id) AS s FROM big GROUP BY grp",
            max_result_rows=3,
        )
    assert "7" in str(excinfo.value)


def test_statement_timeout_interrupts(big_conn):
    """病态大查询超过语句超时 -> 中断取消，连接可复用。"""
    with pytest.raises(QueryTimeoutError):
        execute_sql(
            big_conn,
            "SELECT count(*) FROM big a, big b, big c",
            statement_timeout_ms=300,
        )
    # 中断后连接仍可复用
    assert big_conn.execute("SELECT count(*) FROM big").fetchone()[0] == 200000


def test_engine_error_wrapped(big_conn):
    """DuckDB 引擎报错包装为 SqlExecutionError（可自愈）。"""
    with pytest.raises(SqlExecutionError) as excinfo:
        execute_sql(
            big_conn,
            "SELECT nonexistent_column FROM big",
            max_scan_rows=1000000,
        )
    assert "Binder Error" in str(excinfo.value)


def test_parse_scan_rows():
    txt = (
        "dummy\n"
        "│        100,000 rows       │\n"
        "│        7 rows             │\n"
        "│            0 rows         │"
    )
    assert parse_scan_rows(txt) == 100000
    assert parse_scan_rows("no rows here") == 0


def test_engine_error_wrapped_on_real_execution(big_conn):
    """真实执行阶段的 DuckDB 引擎报错同样包装为 SqlExecutionError（可自愈）。"""
    with pytest.raises(SqlExecutionError) as excinfo:
        execute_sql(big_conn, "SELECT nonexistent_column FROM big")
    assert "Binder Error" in str(excinfo.value)
