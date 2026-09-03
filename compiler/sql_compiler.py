"""确定性 SQL 编译器：QueryDSL -> DuckDB SQL。

设计目标：编译器是完全确定性的纯函数，不接受任何自由 SQL 片段。它只做三件事：
1. 校验 DSL 引用的字段都在语义目录（semantic.catalog）中登记；
2. 按目录声明的连接规则组装 FROM / JOIN；
3. 按受限操作符、聚合函数与枚举生成 SQL，字面量严格转义。

任何未登记字段、非法操作符都会抛出 CompileError，从机制上杜绝 SQL 注入与任意 Join。
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime
from datetime import time as dtime

from config import settings
from semantic.catalog import (
    ALIASES,
    COLUMNS,
    FACT_JOIN_RULES,
    FACT_TABLE,
    JOIN_RULES,
)
from semantic.dsl_schema import (
    AggFunc,
    AggregateMetric,
    Comparison,
    Dimension,
    Filter,
    FilterOperator,
    Granularity,
    Metric,
    QueryDSL,
    RatioMetric,
    RelativeMode,
    RelativeTime,
    RelativeUnit,
    SortDirection,
    TimeFilter,
    TimeRangeType,
)


class CompileError(ValueError):
    """DSL 合法但无法编译为 SQL（例如引用了未登记字段）。"""


# --------------------------------------------------------------------------- #
# 时间窗口解析
# --------------------------------------------------------------------------- #
def _sub_months(d: date, n: int) -> date:
    """按月减法（保持日不变，超出月底则钳到月底）。"""
    total = d.year * 12 + (d.month - 1) - n
    y, m0 = divmod(total, 12)
    m = m0 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))


def _sub_years(d: date, n: int) -> date:
    """按年减法（保持月日不变，闰日钳到 2 月 28）。"""
    y = d.year - n
    last_day = calendar.monthrange(y, d.month)[1]
    return date(y, d.month, min(d.day, last_day))


def _shift_window(
    start: datetime, end: datetime, comparison: Comparison
) -> tuple[datetime, datetime]:
    """将当前窗口整体平移一个对比周期，得到基准窗口。

    - MOM：窗口整体前移一个月；
    - YOY：窗口整体前移一年。
    平移后仍为半开区间 [new_start, new_end)。
    """
    if comparison == Comparison.MOM:
        return (
            datetime.combine(_sub_months(start.date(), 1), start.time()),
            datetime.combine(_sub_months(end.date(), 1), end.time()),
        )
    if comparison == Comparison.YOY:
        return (
            datetime.combine(_sub_years(start.date(), 1), start.time()),
            datetime.combine(_sub_years(end.date(), 1), end.time()),
        )
    raise CompileError(f"不支持的 comparison: {comparison!r}")


def _resolve_window(tf: TimeFilter) -> tuple[datetime, datetime]:
    """将 TimeFilter 解析为半开时间区间 [start, end)。"""
    if tf.range_type == TimeRangeType.ABSOLUTE:
        start = datetime.combine(tf.absolute.start, dtime.min)
        end = datetime.combine(tf.absolute.end, dtime.min)
        return start, end

    rel: RelativeTime = tf.relative
    ref_date: date = tf.reference_date or settings.AS_OF_DATE
    ref = datetime.combine(ref_date, dtime.min)

    if rel.mode == RelativeMode.CALENDAR:
        if rel.unit == RelativeUnit.MONTH:
            start_month = _sub_months(ref_date.replace(day=1), rel.amount)
            end_month = _sub_months(ref_date.replace(day=1), rel.amount - 1)
            return (
                datetime.combine(start_month, dtime.min),
                datetime.combine(end_month, dtime.min),
            )
        if rel.unit == RelativeUnit.YEAR:
            start = date(ref_date.year - rel.amount, 1, 1)
            end = date(ref_date.year - rel.amount + 1, 1, 1)
            return datetime.combine(start, dtime.min), datetime.combine(end, dtime.min)
        raise CompileError("calendar 模式目前仅支持 unit=month/year")

    # trailing 模式
    from datetime import timedelta

    if rel.unit == RelativeUnit.DAY:
        start = ref - timedelta(days=rel.amount)
    elif rel.unit == RelativeUnit.WEEK:
        start = ref - timedelta(days=rel.amount * 7)
    elif rel.unit == RelativeUnit.MONTH:
        start = datetime.combine(_sub_months(ref_date, rel.amount), dtime.min)
    elif rel.unit == RelativeUnit.YEAR:
        start = datetime.combine(
            date(ref_date.year - rel.amount, ref_date.month, ref_date.day), dtime.min
        )
    else:
        raise CompileError(f"不支持的相对时间单位: {rel.unit!r}")
    return start, ref


# --------------------------------------------------------------------------- #
# 字段与字面量
# --------------------------------------------------------------------------- #
def _table_for_field(field: str) -> str:
    meta = COLUMNS.get(field)
    if meta is None:
        raise CompileError(f"未登记的字段: {field!r}，只能引用语义目录中的逻辑字段")
    return meta.table


def _qualify(field: str) -> str:
    meta = COLUMNS.get(field)
    if meta is None:
        raise CompileError(f"未登记的字段: {field!r}")
    return f"{ALIASES[meta.table]}.{meta.column}"


def _check_scalar_type(value, dtype: str) -> None:
    if dtype == "str" and not isinstance(value, str):
        raise CompileError(f"字段要求字符串，收到 {value!r}")
    if dtype == "int" and (isinstance(value, bool) or not isinstance(value, int)):
        raise CompileError(f"字段要求整数，收到 {value!r}")
    if dtype == "float" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise CompileError(f"字段要求数值，收到 {value!r}")
    if dtype == "bool" and not isinstance(value, bool):
        raise CompileError(f"字段要求布尔值，收到 {value!r}")
    if dtype == "timestamp" and not isinstance(value, (str, date, datetime)):
        raise CompileError(f"字段要求日期/时间，收到 {value!r}")


def _literal(value, dtype: str) -> str:
    """将 Python 值安全转义为 SQL 字面量。"""
    _check_scalar_type(value, dtype)
    if dtype == "str":
        return "'" + str(value).replace("'", "''") + "'"
    if dtype == "int":
        return str(int(value))
    if dtype == "float":
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            raise CompileError("浮点字面量不能为 NaN/Inf")
        return repr(v)
    if dtype == "bool":
        return "TRUE" if value else "FALSE"
    if dtype == "timestamp":
        if isinstance(value, datetime):
            return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return f"TIMESTAMP '{value}'"
    raise CompileError(f"未知字面量类型: {dtype!r}")


def _filter_sql(f: Filter) -> str:
    meta = COLUMNS.get(f.field)
    if meta is None:
        raise CompileError(f"未登记的过滤字段: {f.field!r}")
    col = f"{ALIASES[meta.table]}.{meta.column}"
    dtype = meta.dtype

    if f.operator == FilterOperator.IN:
        vals = ", ".join(_literal(v, dtype) for v in f.value)
        return f"{col} IN ({vals})"
    if f.operator == FilterOperator.BETWEEN:
        lo, hi = f.value
        return f"{col} BETWEEN {_literal(lo, dtype)} AND {_literal(hi, dtype)}"

    op_map = {
        FilterOperator.EQ: "=",
        FilterOperator.NE: "<>",
        FilterOperator.GT: ">",
        FilterOperator.GTE: ">=",
        FilterOperator.LT: "<",
        FilterOperator.LTE: "<=",
    }
    return f"{col} {op_map[f.operator]} {_literal(f.value, dtype)}"


def _time_window_sql(tf: TimeFilter) -> str:
    start, end = _resolve_window(tf)
    col = "f.order_time"
    s = start.strftime("%Y-%m-%d %H:%M:%S")
    e = end.strftime("%Y-%m-%d %H:%M:%S")
    return f"{col} >= TIMESTAMP '{s}' AND {col} < TIMESTAMP '{e}'"


# --------------------------------------------------------------------------- #
# 指标 / 维度表达式
# --------------------------------------------------------------------------- #
def _aggregate_metrics(m: Metric) -> list[AggregateMetric]:
    if isinstance(m, RatioMetric):
        return [m.numerator, m.denominator]
    return [m]


def _aggregate_expr(am: AggregateMetric) -> str:
    col = _qualify(am.field)
    if am.agg == AggFunc.SUM:
        return f"SUM({col})"
    if am.agg == AggFunc.COUNT:
        return f"COUNT({col})"
    if am.agg == AggFunc.COUNT_DISTINCT:
        return f"COUNT(DISTINCT {col})"
    if am.agg == AggFunc.AVG:
        return f"AVG({col})"
    if am.agg == AggFunc.MIN:
        return f"MIN({col})"
    if am.agg == AggFunc.MAX:
        return f"MAX({col})"
    raise CompileError(f"不支持的聚合函数: {am.agg!r}")


def _metric_expr(m: Metric) -> tuple[str, str]:
    if isinstance(m, RatioMetric):
        num = _aggregate_expr(m.numerator)
        den = _aggregate_expr(m.denominator)
        return f"({num}) / ({den})", m.alias
    return _aggregate_expr(m), m.alias


def _dimension_expr(d: Dimension, granularity: Granularity) -> tuple[str, str]:
    meta = COLUMNS.get(d.field)
    if meta is None:
        raise CompileError(f"未登记的维度字段: {d.field!r}")
    alias = d.alias or d.field
    qual = f"{ALIASES[meta.table]}.{meta.column}"
    if d.field == "order_time":
        expr = f"date_trunc('{granularity.value}', {qual})"
    else:
        expr = qual
    return expr, alias


# --------------------------------------------------------------------------- #
# 表集合与 FROM/JOIN
# --------------------------------------------------------------------------- #
def _collect_tables(dsl: QueryDSL) -> set[str]:
    """收集 DSL 引用到的所有表（用于决定 JOIN 哪些维度表）。"""
    tables: set[str] = set()
    for d in dsl.dimensions:
        tables.add(_table_for_field(d.field))
    for f in dsl.filters:
        tables.add(_table_for_field(f.field))
    for m in dsl.metrics:
        for am in _aggregate_metrics(m):
            tables.add(_table_for_field(am.field))
    return tables


def _from_clause(dsl: QueryDSL) -> str:
    tables = _collect_tables(dsl)
    sql = f"FROM {FACT_TABLE} f"
    joins: list[str] = []
    # 维度表受控连接
    for dim in ("dim_user", "dim_product"):
        if dim in tables:
            joins.append(JOIN_RULES[dim])
    # 第二事实表受控连接（1:1 LEFT JOIN，无扇出放大）
    for fact, rule in FACT_JOIN_RULES.items():
        if fact in tables:
            joins.append(rule)
    if joins:
        sql += "\n" + "\n".join(joins)
    return sql


# --------------------------------------------------------------------------- #
# 对比（同比/环比）编译
# --------------------------------------------------------------------------- #
def _compile_with_comparison(dsl: QueryDSL) -> str:
    """编译带 comparison 的 DSL：当前窗口 vs 基准窗口，输出增长率。

    输出约定（以指标别名为 gmv、comparison=YOY 为例）：
        gmv      当前周期值
        gmv_prev 基准周期值
        gmv_yoy  增长率 = (cur - prev) / NULLIF(prev, 0)
    对 MOM 同理生成 {alias}_mom。
    """
    tf = dsl.time_filter
    assert tf is not None and tf.comparison != Comparison.NONE

    cur_start, cur_end = _resolve_window(tf)
    prev_start, prev_end = _shift_window(cur_start, cur_end, tf.comparison)
    cmp_suffix = "_mom" if tf.comparison == Comparison.MOM else "_yoy"

    def _window_block(label: str, start: datetime, end: datetime) -> str:
        metrics_sql = ", ".join(
            f"{_metric_expr(m)[0]} AS {_metric_expr(m)[1]}" for m in dsl.metrics
        )
        where = [_filter_sql(f) for f in dsl.filters]
        s = start.strftime("%Y-%m-%d %H:%M:%S")
        e = end.strftime("%Y-%m-%d %H:%M:%S")
        where.append(f"f.order_time >= TIMESTAMP '{s}' AND f.order_time < TIMESTAMP '{e}'")
        return (
            f"{label} AS (\n"
            f"  SELECT {metrics_sql}\n"
            f"  {_from_clause(dsl)}\n"
            "  WHERE " + " AND ".join(where) + "\n"
            ")"
        )

    selects: list[str] = []
    for m in dsl.metrics:
        alias = m.alias
        selects.append(f"cur.{alias} AS {alias}")
        selects.append(f"prev.{alias} AS {alias}_prev")
        selects.append(
            f"(cur.{alias} - prev.{alias}) / NULLIF(prev.{alias}, 0) AS {alias}{cmp_suffix}"
        )

    sql = "WITH " + _window_block("cur", cur_start, cur_end)
    sql += ",\n" + _window_block("prev", prev_start, prev_end)
    sql += "\nSELECT " + ", ".join(selects)
    sql += "\nFROM cur, prev"

    # 排序字段：允许引用当前/基准/增长率列
    allowed = set()
    for m in dsl.metrics:
        allowed.add(m.alias)
        allowed.add(m.alias + "_prev")
        allowed.add(m.alias + cmp_suffix)
    if dsl.order_by:
        parts: list[str] = []
        for o in dsl.order_by:
            if o.field not in allowed:
                raise CompileError(f"order_by 字段 {o.field!r} 不是指标别名或对比列")
            direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
            parts.append(f"{o.field} {direction}")
        sql += "\nORDER BY " + ", ".join(parts)

    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 主编译入口
# --------------------------------------------------------------------------- #
def compile_sql(dsl: QueryDSL) -> str:
    """将 QueryDSL 编译为 DuckDB SQL 字符串（确定性、无注入）。"""
    tf = dsl.time_filter
    if tf is not None and tf.comparison != Comparison.NONE:
        return _compile_with_comparison(dsl)

    # 1. FROM + JOIN（含第二事实表受控连接，由 _from_clause 内部收集表）
    from_clause = _from_clause(dsl)

    # 3. SELECT 列表
    granularity = tf.granularity if tf else Granularity.DAY
    selects: list[str] = []
    dim_exprs: list[str] = []
    dim_aliases: set[str] = set()
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        selects.append(f"{expr} AS {alias}")
        dim_exprs.append(expr)
        dim_aliases.add(alias)

    metric_aliases: set[str] = set()
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        selects.append(f"{expr} AS {alias}")
        metric_aliases.add(alias)

    # 4. WHERE
    where: list[str] = []
    for f in dsl.filters:
        where.append(_filter_sql(f))
    if tf is not None:
        where.append(_time_window_sql(tf))

    # 5. 组装
    sql = "SELECT " + ", ".join(selects)
    sql += "\n" + from_clause
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    if dim_exprs:
        sql += "\nGROUP BY " + ", ".join(dim_exprs)

    # 6. ORDER BY
    if dsl.order_by:
        parts: list[str] = []
        for o in dsl.order_by:
            if o.field in metric_aliases or o.field in dim_aliases:
                ref = o.field
            else:
                raise CompileError(f"order_by 字段 {o.field!r} 不是指标别名或维度别名")
            direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
            parts.append(f"{ref} {direction}")
        sql += "\nORDER BY " + ", ".join(parts)

    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql
