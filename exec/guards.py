"""SQL 执行层资源治理与自愈支持（P0/P1）。

本模块把"执行 SQL"封装为带资源护栏的受控操作，供 web.service 的完整链路使用：

1. statement_timeout / 查询超时取消
   DuckDB 1.5.x 不提供 max_execution_time 设置，因此在 Python 侧实现看门狗：
   查询在独立工作线程内执行，主线程等待超时阈值；一旦超时调用
   conn.interrupt() 强制取消（DuckDB 抛 InterruptException），翻译为
   QueryTimeoutError。

2. 扫描行数上限（扫描行熔断）
   DuckDB 没有原生的"扫描行数上限"，故采用 EXPLAIN ANALYZE 预检：
   先以分析执行的方式得到每个物理算子实际处理的行数（TABLE_SCAN 的 rows），
   若任一基表扫描超过 max_scan_rows 则直接熔断（MaxRowsScannedExceeded），
   不再执行真实查询。预检在超时看门狗内运行，病态查询同样会被超时拦截。
   注意：EXPLAIN ANALYZE 会实际执行查询，这是"先计量后放行"的成本，
   也是 DuckDB 上实现预防式扫描上限的唯一可靠手段。

3. LIMIT 硬上限熔断（返回行数熔断）
   无论 DSL 的 limit 是多少，执行层对返回行数做独立硬上限 max_result_rows
   （防御性校验，不依赖 schema 约束），超限抛 ResultLimitExceeded。

可恢复的执行错误统一抛 SqlExecutionError（含精确的引擎报错信息），
上层自愈循环可将其喂回 LLM 重写 DSL。所有执行错误都可安全序列化为字符串。
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb

# --------------------------------------------------------------------------- #
# 异常体系
# --------------------------------------------------------------------------- #


class SqlExecutionError(RuntimeError):
    """SQL 执行失败（可自愈：精确报错可喂回 LLM 重写 DSL 后重试）。"""

    code = "sql_execution_error"


class QueryTimeoutError(SqlExecutionError):
    """查询超过 statement_timeout，已被中断取消。"""

    code = "query_timeout"


class MaxRowsScannedExceeded(SqlExecutionError):
    """任一基表扫描行数超过上限，熔断拒绝执行。"""

    code = "max_rows_scanned_exceeded"


class ResultLimitExceeded(SqlExecutionError):
    """返回行数超过 LIMIT 硬上限，熔断拒绝放行结果。"""

    code = "result_limit_exceeded"


class UnsafeSqlError(RuntimeError):
    """SQL 未通过只读语句白名单，拒绝执行且不进入自愈。"""

    code = "unsafe_sql"


_FORBIDDEN_SQL_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "PRAGMA",
}


def _strip_sql_comments(sql: str) -> str:
    """移除 SQL 注释，同时保留字符串内容，避免误判字面量。"""
    out: list[str] = []
    i = 0
    while i < len(sql):
        if sql.startswith("--", i):
            i += 2
            while i < len(sql) and sql[i] != "\n":
                i += 1
        elif sql.startswith("/*", i):
            i += 2
            while i < len(sql) and not sql.startswith("*/", i):
                i += 1
            i = min(i + 2, len(sql))
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _sql_code_without_literals(sql: str) -> str:
    """将单引号字符串替换为空，供语句结构检查使用。"""
    out: list[str] = []
    i = 0
    while i < len(sql):
        if sql[i] == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def assert_read_only_sql(sql: str) -> None:
    """硬断言 SQL 是单条 SELECT/WITH，拒绝 DDL/DML 与多语句。"""
    code = _sql_code_without_literals(_strip_sql_comments(sql))
    tokens = code.strip().split()
    if not tokens or tokens[0].upper() not in {"SELECT", "WITH"}:
        raise UnsafeSqlError("仅允许执行 SELECT/WITH 只读查询")
    if ";" in code:
        raise UnsafeSqlError("拒绝执行多语句 SQL")
    upper = code.upper()
    for keyword in _FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            raise UnsafeSqlError(f"检测到被禁止的 SQL 关键字: {keyword}")


# --------------------------------------------------------------------------- #
# 执行结果
# --------------------------------------------------------------------------- #


@dataclass
class ExecutionResult:
    """一次受控执行的完整结果。"""

    columns: list[str]
    rows: list[list[Any]]
    scan_rows: int = 0
    duration_ms: float = 0.0


# --------------------------------------------------------------------------- #
# EXPLAIN ANALYZE 解析
# --------------------------------------------------------------------------- #

# TABLE_SCAN 算子块：每个物理算子块末尾都含一行形如 "        100,000 rows" 的
# 行数记录。取所有算子行数的最大值作为"扫描行数"预算口径。
_ROWS_LINE_RE = re.compile(r"(\d[\d,]*)\s+rows", re.MULTILINE)


def parse_scan_rows(plan_text: str) -> int:
    """从 EXPLAIN ANALYZE / profiling 文本中解析最大单算子处理行数。

    DuckDB 的算子块以 box-drawing 字符包围，行形如 "│ 100,000 rows │"，
    因此用非锚定匹配提取所有 "N rows"，取最大值作为"扫描行数"预算口径
    （对多表/CTE 查询即各基表扫描的最大值）。解析失败（无行数记录）返回 0。
    """
    counts: list[int] = []
    for match in _ROWS_LINE_RE.finditer(plan_text):
        counts.append(int(match.group(1).replace(",", "")))
    return max(counts) if counts else 0


# --------------------------------------------------------------------------- #
# 超时看门狗
# --------------------------------------------------------------------------- #


def _run_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    timeout_ms: int | None,
) -> tuple[list[str], list[list[Any]]]:
    """在工作线程中执行查询并取回结果；超时则 interrupt() 取消。

    返回 (columns, rows)。连接在中断后可继续复用（DuckDB 保证）。
    """
    if timeout_ms is None:
        cur = conn.execute(sql)
        columns = tuple(d[0] for d in cur.description)
        rows = [list(row) for row in cur.fetchall()]
        return list(columns), rows

    outcome: dict[str, Any] = {}

    def _worker() -> None:
        try:
            cur = conn.execute(sql)
            outcome["columns"] = tuple(d[0] for d in cur.description)
            outcome["rows"] = [list(row) for row in cur.fetchall()]
        except BaseException as exc:  # 线程内任意异常均须回传
            outcome["exc"] = exc

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout_ms / 1000.0)

    if worker.is_alive():
        # 超时：强制中断，等待线程真正退出
        try:
            conn.interrupt()
        except Exception:  # interrupt 失败也须继续走超时分支
            pass
        worker.join(2.0)
        raise QueryTimeoutError(f"查询超时（>{timeout_ms}ms），已强制取消")

    if "exc" in outcome:
        raise outcome["exc"]
    return list(outcome["columns"]), outcome["rows"]


# --------------------------------------------------------------------------- #
# 受控执行入口
# --------------------------------------------------------------------------- #


def execute_sql(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    statement_timeout_ms: int | None = None,
    max_scan_rows: int | None = None,
    max_result_rows: int | None = None,
) -> ExecutionResult:
    """带资源护栏执行一条只读 SELECT。

    参数（None 表示不启用该护栏）：
    - statement_timeout_ms：语句超时（毫秒），超时中断并抛 QueryTimeoutError；
    - max_scan_rows：扫描行数上限，预检超过即抛 MaxRowsScannedExceeded；
    - max_result_rows：返回行数硬上限，超过即抛 ResultLimitExceeded。
    """
    assert_read_only_sql(sql)
    started = time.perf_counter()
    scan_rows = 0

    # 1) 扫描行预算预检（EXPLAIN ANALYZE 在超时看门狗内运行）
    if max_scan_rows is not None:
        plan_sql = "EXPLAIN ANALYZE " + sql
        try:
            _, plan_rows = _run_with_timeout(conn, plan_sql, statement_timeout_ms)
        except QueryTimeoutError:
            raise
        except duckdb.Error as exc:
            # 预检阶段暴露的引擎错误同样视为执行失败（可自愈）
            raise SqlExecutionError(f"{type(exc).__name__}: {exc}") from exc
        plan_text = (
            str(plan_rows[0][1])
            if plan_rows and len(plan_rows[0]) > 1
            else (str(plan_rows[0][0]) if plan_rows else "")
        )
        scanned = parse_scan_rows(plan_text)
        scan_rows = scanned
        if scanned > max_scan_rows:
            raise MaxRowsScannedExceeded(
                f"扫描行数上限熔断：本次查询扫描 {scanned} 行，超过上限 {max_scan_rows}"
            )

    # 2) 真实执行（同样受超时护栏保护）；引擎报错统一包装为 SqlExecutionError（可自愈）
    try:
        columns, rows = _run_with_timeout(conn, sql, statement_timeout_ms)
    except QueryTimeoutError:
        raise
    except duckdb.Error as exc:
        raise SqlExecutionError(f"{type(exc).__name__}: {exc}") from exc

    # 3) 返回行数硬上限
    if max_result_rows is not None and len(rows) > max_result_rows:
        raise ResultLimitExceeded(
            f"LIMIT 硬上限熔断：返回 {len(rows)} 行，超过上限 {max_result_rows}"
        )

    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return ExecutionResult(columns=columns, rows=rows, scan_rows=scan_rows, duration_ms=duration_ms)
