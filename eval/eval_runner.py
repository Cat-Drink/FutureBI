"""自动化评测骨架。

链路：自然语言 -> DSL (run_pipeline 插槽) -> 确定性 SQL (compiler) -> DuckDB 执行。

对每个 golden 用例做两类断言：
1. 结构断言：run_pipeline 返回的 QueryDSL 与预期 DSL 完全一致；
2. 结果断言：编译出的 SQL 与 golden 标准 SQL 在 DuckDB 执行后结果集完全一致
   （列名一致 + 行集一致，sha256 结果哈希便于回归比对）。

用法：
    python -m eval.eval_runner              # 连接项目根目录的 duckdb 文件跑全量
    python -m eval.eval_runner --print-sql  # 额外打印每个用例的编译 SQL
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import duckdb

from agent.pipeline import run_pipeline as _production_pipeline  # noqa: F401  (接口保留)
from compiler.sql_compiler import compile_sql
from config import settings
from semantic.dsl_schema import QueryDSL

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_dataset.json"


# --------------------------------------------------------------------------- #
# Golden Dataset 加载
# --------------------------------------------------------------------------- #
def load_golden() -> list[dict[str, Any]]:
    """加载 golden_dataset.json，返回用例列表（含 question/dsl/sql）。"""
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("golden_dataset.json 顶层必须是 JSON 数组")
    return data


# --------------------------------------------------------------------------- #
# run_pipeline 插槽（默认 golden oracle）
# --------------------------------------------------------------------------- #
def _golden_oracle(query: str) -> QueryDSL:
    """在未接入 LLM 前，直接按问题文本匹配 golden 中的预期 DSL，用于自闭环评测。"""
    for item in load_golden():
        if item["question"] == query:
            return QueryDSL.model_validate(item["dsl"])
    raise KeyError(f"golden dataset 中未找到问题: {query!r}")


def run_pipeline(query: str) -> QueryDSL:
    """自然语言 -> QueryDSL 插槽。

    生产环境将替换为 agent.pipeline.run_pipeline（LLM 产出 + 严格校验）。
    评测自闭环阶段默认使用 golden oracle。
    """
    return _golden_oracle(query)


# --------------------------------------------------------------------------- #
# 结果提取与哈希
# --------------------------------------------------------------------------- #
def execute(
    conn: duckdb.DuckDBPyConnection, sql: str
) -> tuple[tuple[str, ...], tuple[tuple, ...]]:
    """执行 SQL，返回 (列名元组, 行元组)。"""
    cur = conn.execute(sql)
    columns = tuple(d[0] for d in cur.description)
    rows = tuple(tuple(r) for r in cur.fetchall())
    return columns, rows


def result_hash(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> str:
    """结果集哈希（sha256），用于回归比对。"""
    payload = repr({"columns": columns, "rows": rows}).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.strip().lower().split())


# --------------------------------------------------------------------------- #
# 评测
# --------------------------------------------------------------------------- #
@dataclass
class CaseReport:
    id: str
    question: str
    dsl_ok: bool
    result_ok: bool
    sql_ok: bool
    hash: str
    error: str | None = None
    compiled_sql: str = ""
    golden_sql: str = ""


@dataclass
class EvalSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    reports: list[CaseReport] = field(default_factory=list)


def evaluate_case(
    conn: duckdb.DuckDBPyConnection,
    item: dict[str, Any],
    pipeline: Callable[[str], QueryDSL] = run_pipeline,
) -> CaseReport:
    """评测单个用例。"""
    case_id = item.get("id", "?")
    question = item["question"]
    expected_dsl = QueryDSL.model_validate(item["dsl"])
    golden_sql = item["sql"]

    report = CaseReport(
        id=case_id,
        question=question,
        dsl_ok=False,
        result_ok=False,
        sql_ok=False,
        hash="",
        golden_sql=golden_sql,
    )

    try:
        # 1. 结构断言：run_pipeline 返回的 DSL 与预期一致
        actual_dsl = pipeline(question)
        report.dsl_ok = (actual_dsl == expected_dsl)

        # 2. 编译 DSL -> SQL
        compiled_sql = compile_sql(actual_dsl)
        report.compiled_sql = compiled_sql
        report.sql_ok = _normalize_sql(compiled_sql) == _normalize_sql(golden_sql)

        # 3. 结果断言：标准 SQL 与编译 SQL 执行结果一致。
        #    无 ORDER BY 时行序不确定，因此按"列名 + 排序后行集"做集合判等；
        #    对带 ORDER BY 的用例（如 Top N）排序判等依然正确。
        g_cols, g_rows = execute(conn, golden_sql)
        c_cols, c_rows = execute(conn, compiled_sql)
        report.hash = result_hash(c_cols, c_rows)
        report.result_ok = (g_cols == c_cols) and (sorted(g_rows) == sorted(c_rows))
    except Exception as exc:  # noqa: BLE001 - 评测要捕获并呈现单用例异常
        report.error = f"{type(exc).__name__}: {exc}"

    return report


def evaluate_all(
    conn: duckdb.DuckDBPyConnection,
    pipeline: Callable[[str], QueryDSL] = run_pipeline,
) -> EvalSummary:
    summary = EvalSummary()
    for item in load_golden():
        summary.total += 1
        report = evaluate_case(conn, item, pipeline=pipeline)
        summary.reports.append(report)
        ok = report.dsl_ok and report.result_ok and report.sql_ok
        if ok and report.error is None:
            summary.passed += 1
        else:
            summary.failed += 1
    return summary


def _print_summary(summary: EvalSummary, print_sql: bool = False) -> None:
    print("=" * 90)
    print(f"评测结果: {summary.passed}/{summary.total} 通过")
    print("=" * 90)
    for r in summary.reports:
        ok = r.dsl_ok and r.result_ok and r.sql_ok and r.error is None
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {r.id}  {r.question}")
        if not ok:
            if r.error:
                print(f"       错误: {r.error}")
            else:
                print(f"        dsl_ok={r.dsl_ok}  sql_ok={r.sql_ok}  result_ok={r.result_ok}")
        print(f"       结果哈希: {r.hash}")
        if print_sql:
            print(f"       编译SQL:\n{r.compiled_sql}")
    print("=" * 90)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChatBI Golden Dataset 评测")
    parser.add_argument("--print-sql", action="store_true", help="打印每个用例的编译 SQL")
    args = parser.parse_args(argv)

    if not settings.DB_PATH.exists():
        raise SystemExit(
            f"未找到数仓文件 {settings.DB_PATH}，请先执行: python -m mock.init_duckdb"
        )

    conn = duckdb.connect(str(settings.DB_PATH), read_only=True)
    try:
        summary = evaluate_all(conn)
    finally:
        conn.close()

    _print_summary(summary, print_sql=args.print_sql)
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
