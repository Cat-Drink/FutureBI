"""确定性 SQL 编译器：QueryDSL -> DuckDB SQL。

设计目标：编译器是完全确定性的纯函数，不接受任何自由 SQL 片段。它只做三件事：
1. 校验 DSL 引用的字段都在语义目录（semantic.catalog）中登记；
2. 按目录声明的连接规则组装 FROM / JOIN；
3. 按受限操作符、聚合函数与枚举生成 SQL，字面量严格转义。

任何未登记字段、非法操作符都会抛出 CompileError，从机制上杜绝 SQL 注入与任意 Join。

支持的语义能力：
- 聚合 / 比率指标、维度分组、过滤、排序、限行；
- 同比/环比（comparison，cur/prev 双窗口 CTE）；
- 窗口函数（累计求和 cumsum / 移动平均 moving_avg）；
- 日期连续补零（fill_gaps）；
- 分组 Top-N（top_n，ROW_NUMBER 分区过滤）。
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta
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
    TopN,
    WindowFunc,
    WindowMetric,
)


class CompileError(ValueError):
    """DSL 合法但无法编译为 SQL（例如引用了未登记字段）。"""


# 时间维度逻辑字段（窗口函数 / 补零排序所依赖）
TIME_FIELDS = frozenset({"order_time", "refund_time", "register_time"})


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
    if isinstance(m, WindowMetric):
        return [m.base]
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
    """聚合/比率指标 -> (表达式, 别名)。窗口指标由 _window_expr 另行处理。"""
    if isinstance(m, WindowMetric):
        raise CompileError("窗口指标不能在此上下文直接展开")
    if isinstance(m, RatioMetric):
        num = _aggregate_expr(m.numerator)
        den = _aggregate_expr(m.denominator)
        return f"({num}) / ({den})", m.alias
    return _aggregate_expr(m), m.alias


def _window_expr(wm: WindowMetric, order_expr: str) -> str:
    """窗口指标 -> 窗口函数表达式（在时间维度上滚动计算）。"""
    base = _aggregate_expr(wm.base)
    if wm.func == WindowFunc.CUMSUM:
        return f"SUM({base}) OVER (ORDER BY {order_expr})"
    if wm.func == WindowFunc.MOVING_AVG:
        n = wm.window_size
        return (
            f"AVG({base}) OVER (ORDER BY {order_expr} "
            f"ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)"
        )
    raise CompileError(f"不支持的窗口函数: {wm.func!r}")


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


def _time_dimension_expr(dsl: QueryDSL, granularity: Granularity) -> tuple[str, str]:
    """找出 DSL 中的唯一时间维度，返回 (表达式, 别名)；无/多时间维度则报错。"""
    time_dims = [d for d in dsl.dimensions if d.field in TIME_FIELDS]
    if not time_dims:
        raise CompileError("窗口/补零查询需要时间维度（order_time/refund_time/register_time）")
    if len(time_dims) > 1:
        raise CompileError("一次查询最多一个时间维度")
    return _dimension_expr(time_dims[0], granularity)


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
    对 MOM 同理生成 {alias}_mom。多指标时逐个指标生成三列。

    支持非时间维度（品类/品牌/省份等）：cur/prev 两个 CTE 各自按维度分组，
    外层以维度列 LEFT JOIN 配对，输出 维度列 + 当前值 + 基准值 + 增长率。
    时间维度（order_time 等）与对比的组合需要"按位配对"（如 6月1日 对 5月1日），
    该语义未定义，显式抛 CompileError 而非静默丢弃维度（正确性缺陷修复）。
    """
    tf = dsl.time_filter
    assert tf is not None and tf.comparison != Comparison.NONE
    granularity = tf.granularity

    if any(d.field in TIME_FIELDS for d in dsl.dimensions):
        raise CompileError(
            "comparison 暂不支持时间维度（order_time/refund_time/register_time）："
            "时间维度需要按位配对（当前日期 vs 基准周期对应日期），"
            "请改用品类/品牌/省份等分组维度"
        )

    cur_start, cur_end = _resolve_window(tf)
    prev_start, prev_end = _shift_window(cur_start, cur_end, tf.comparison)
    cmp_suffix = "_mom" if tf.comparison == Comparison.MOM else "_yoy"

    # 维度表达式与别名（cur/prev 两 CTE 共用，保证可配对）
    dim_exprs: list[str] = []
    dim_aliases: list[str] = []
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        dim_exprs.append(expr)
        dim_aliases.append(alias)

    def _window_block(label: str, start: datetime, end: datetime) -> str:
        select_items: list[str] = []
        for expr, alias in zip(dim_exprs, dim_aliases, strict=False):
            select_items.append(f"{expr} AS {alias}")
        for m in dsl.metrics:
            select_items.append(f"{_metric_expr(m)[0]} AS {_metric_expr(m)[1]}")
        where = [_filter_sql(f) for f in dsl.filters]
        s = start.strftime("%Y-%m-%d %H:%M:%S")
        e = end.strftime("%Y-%m-%d %H:%M:%S")
        where.append(f"f.order_time >= TIMESTAMP '{s}' AND f.order_time < TIMESTAMP '{e}'")
        block = (
            f"{label} AS (\n"
            f"  SELECT {', '.join(select_items)}\n"
            f"  {_from_clause(dsl)}\n"
            "  WHERE " + " AND ".join(where) + "\n"
        )
        if dim_exprs:
            block += "  GROUP BY " + ", ".join(dim_exprs) + "\n"
        block += ")"
        return block

    selects: list[str] = []
    for alias in dim_aliases:
        selects.append(f"cur.{alias} AS {alias}")
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
    if dim_aliases:
        sql += "\nFROM cur LEFT JOIN prev USING (" + ", ".join(dim_aliases) + ")"
    else:
        sql += "\nFROM cur, prev"

    # 排序字段：允许引用维度列 / 当前值 / 基准值 / 增长率列
    allowed = set(dim_aliases)
    for m in dsl.metrics:
        allowed.add(m.alias)
        allowed.add(m.alias + "_prev")
        allowed.add(m.alias + cmp_suffix)
    if dsl.order_by:
        parts: list[str] = []
        for o in dsl.order_by:
            if o.field not in allowed:
                raise CompileError(f"order_by 字段 {o.field!r} 不是维度/指标别名或对比列")
            direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
            parts.append(f"{o.field} {direction}")
        sql += "\nORDER BY " + ", ".join(parts)

    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 分组 Top-N 编译
# --------------------------------------------------------------------------- #
def _compile_with_top_n(dsl: QueryDSL) -> str:
    """编译分组 Top-N：内层聚合 + ROW_NUMBER 分区排序，外层过滤序号 <= n。

    例 "每省 GMV Top 3 品类"：
        dimensions = [province, category]，top_n.n=3，partition_by=[province]，
        order_by=[{gmv desc}]。
    """
    top: TopN = dsl.top_n
    granularity = dsl.time_filter.granularity if dsl.time_filter else Granularity.DAY

    dim_expr_by_field: dict[str, str] = {}
    dim_alias_by_field: dict[str, str] = {}
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        dim_expr_by_field[d.field] = expr
        dim_alias_by_field[d.field] = alias

    metric_expr_by_alias: dict[str, str] = {}
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        metric_expr_by_alias[alias] = expr

    # 分区字段：必须是维度字段
    partition_exprs: list[str] = []
    partition_aliases: list[str] = []
    for field in top.partition_by:
        if field not in dim_expr_by_field:
            raise CompileError(f"top_n.partition_by 字段 {field!r} 不是维度字段")
        partition_exprs.append(dim_expr_by_field[field])
        partition_aliases.append(dim_alias_by_field[field])

    # 排序字段：指标别名或维度字段
    order_exprs: list[str] = []
    for o in top.order_by:
        if o.field in metric_expr_by_alias:
            expr = metric_expr_by_alias[o.field]
        elif o.field in dim_expr_by_field:
            expr = dim_expr_by_field[o.field]
        else:
            raise CompileError(f"top_n.order_by 字段 {o.field!r} 不是指标别名或维度字段")
        direction = "ASC" if o.direction == SortDirection.ASC else "DESC"
        order_exprs.append(f"{expr} {direction}")

    inner_selects: list[str] = []
    inner_group: list[str] = []
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        inner_selects.append(f"{expr} AS {alias}")
        inner_group.append(expr)
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        inner_selects.append(f"{expr} AS {alias}")

    where = [_filter_sql(f) for f in dsl.filters]
    if dsl.time_filter is not None:
        where.append(_time_window_sql(dsl.time_filter))

    inner_sql = "SELECT " + ", ".join(inner_selects)
    inner_sql += ",\n         ROW_NUMBER() OVER (PARTITION BY " + ", ".join(partition_exprs)
    inner_sql += " ORDER BY " + ", ".join(order_exprs) + ") AS __rn"
    inner_sql += "\n" + _from_clause(dsl)
    if where:
        inner_sql += "\nWHERE " + " AND ".join(where)
    inner_sql += "\nGROUP BY " + ", ".join(inner_group)

    # 外层：去掉 __rn，过滤序号
    outer_selects = [dim_alias_by_field[d.field] for d in dsl.dimensions]
    outer_selects += [m.alias for m in dsl.metrics]

    outer_order = [f"{alias} ASC" for alias in partition_aliases]
    outer_order += [
        (f"{o.field} ASC" if o.direction == SortDirection.ASC else f"{o.field} DESC")
        for o in top.order_by
    ]

    sql = "SELECT " + ", ".join(outer_selects)
    sql += "\nFROM (\n" + inner_sql + "\n) AS __ranked"
    sql += f"\nWHERE __rn <= {int(top.n)}"
    sql += "\nORDER BY " + ", ".join(outer_order)
    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 日期连续补零编译
# --------------------------------------------------------------------------- #
def _compile_with_fill_gaps(dsl: QueryDSL) -> str:
    """编译日期补零：时间序列 spine LEFT JOIN 聚合结果，缺值填 0。

    要求：唯一时间维度 + 明确时间窗口（absolute 或可解析的 relative）。
    支持 day / month 粒度；week 粒度暂不支持补零。
    """
    if dsl.time_filter is None:
        raise CompileError("fill_gaps 需要明确的时间窗口（time_filter）")
    tf = dsl.time_filter
    granularity = tf.granularity
    start, end = _resolve_window(tf)

    time_expr, time_alias = _time_dimension_expr(dsl, granularity)

    # spine 步长与闭区间终点
    if granularity == Granularity.DAY:
        step = "INTERVAL 1 DAY"
        end_incl = end - timedelta(days=1)
    elif granularity == Granularity.MONTH:
        step = "INTERVAL 1 MONTH"
        end_incl = datetime.combine(_sub_months(end.date(), 1), end.time())
    else:
        raise CompileError(f"fill_gaps 暂不支持 granularity={granularity.value}（仅 day/month）")

    # 内层聚合：时间维度 + 指标（聚合/比率均可）
    inner_selects: list[str] = [f"{time_expr} AS {time_alias}"]
    inner_group: list[str] = [time_expr]
    metric_aliases: list[str] = []
    for m in dsl.metrics:
        expr, alias = _metric_expr(m)
        inner_selects.append(f"{expr} AS {alias}")
        metric_aliases.append(alias)

    where = [_filter_sql(f) for f in dsl.filters]
    where.append(_time_window_sql(tf))

    start_s = start.strftime("%Y-%m-%d %H:%M:%S")
    end_incl_s = end_incl.strftime("%Y-%m-%d %H:%M:%S")

    spine = (
        f"__spine AS (\n"
        f"  SELECT UNNEST(generate_series(TIMESTAMP '{start_s}', TIMESTAMP '{end_incl_s}', {step})) "
        f"AS {time_alias}\n"
        ")"
    )
    agg = (
        "__agg AS (\n"
        "  SELECT " + ", ".join(inner_selects) + "\n"
        f"  {_from_clause(dsl)}\n"
        "  WHERE " + " AND ".join(where) + "\n"
        "  GROUP BY " + ", ".join(inner_group) + "\n"
        ")"
    )

    # 外层：spine LEFT JOIN agg，指标 COALESCE 为 0
    outer_selects: list[str] = [f"s.{time_alias} AS {time_alias}"]
    for alias in metric_aliases:
        outer_selects.append(f"COALESCE(a.{alias}, 0) AS {alias}")

    sql = "WITH " + spine + ",\n" + agg
    sql += "\nSELECT " + ", ".join(outer_selects)
    sql += f"\nFROM __spine s\nLEFT JOIN __agg a ON a.{time_alias} = s.{time_alias}"
    sql += f"\nORDER BY s.{time_alias} ASC"
    sql += f"\nLIMIT {int(dsl.limit)}"
    return sql


# --------------------------------------------------------------------------- #
# 主编译入口
# --------------------------------------------------------------------------- #
def compile_sql(dsl: QueryDSL) -> str:
    """将 QueryDSL 编译为 DuckDB SQL 字符串（确定性、无注入）。"""
    tf = dsl.time_filter
    has_window = any(isinstance(m, WindowMetric) for m in dsl.metrics)

    # 互斥校验：comparison / top_n / fill_gaps / window 属于不同查询形态
    comparison = tf.comparison if tf is not None else Comparison.NONE
    if comparison != Comparison.NONE:
        if has_window or dsl.fill_gaps or dsl.top_n is not None:
            raise CompileError("comparison 不能与窗口指标/补零/分组 Top-N 同时使用")
        return _compile_with_comparison(dsl)

    if dsl.top_n is not None:
        if has_window or dsl.fill_gaps:
            raise CompileError("分组 Top-N 不能与窗口指标/补零同时使用")
        return _compile_with_top_n(dsl)

    if dsl.fill_gaps:
        if has_window:
            raise CompileError("日期补零不能与窗口指标同时使用")
        return _compile_with_fill_gaps(dsl)

    # ---- 普通路径（聚合/比率/窗口指标） ----
    from_clause = _from_clause(dsl)
    granularity = tf.granularity if tf else Granularity.DAY

    selects: list[str] = []
    dim_exprs: list[str] = []
    dim_aliases: set[str] = set()
    time_dim_expr: str | None = None
    for d in dsl.dimensions:
        expr, alias = _dimension_expr(d, granularity)
        selects.append(f"{expr} AS {alias}")
        dim_exprs.append(expr)
        dim_aliases.add(alias)
        if d.field in TIME_FIELDS:
            time_dim_expr = expr

    metric_aliases: set[str] = set()
    for m in dsl.metrics:
        if isinstance(m, WindowMetric):
            if time_dim_expr is None:
                raise CompileError("窗口指标需要时间维度（order_time/refund_time/register_time）")
            expr = _window_expr(m, time_dim_expr)
            alias = m.alias
        else:
            expr, alias = _metric_expr(m)
        selects.append(f"{expr} AS {alias}")
        metric_aliases.add(alias)

    where: list[str] = []
    for f in dsl.filters:
        where.append(_filter_sql(f))
    if tf is not None:
        where.append(_time_window_sql(tf))

    sql = "SELECT " + ", ".join(selects)
    sql += "\n" + from_clause
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    if dim_exprs:
        sql += "\nGROUP BY " + ", ".join(dim_exprs)

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
