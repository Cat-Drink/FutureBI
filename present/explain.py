"""DSL -> 自然语言解释（确定性、可复现、零幻觉）。

把结构化 QueryDSL 翻译成一句中文业务话术，便于用户理解"系统到底查了什么"。
不涉及任何 LLM，纯字符串拼接，输出稳定可测。
"""

from __future__ import annotations

from present.labels import (
    agg_label,
    field_label,
    op_label,
    value_label,
)
from semantic.dsl_schema import (
    QueryDSL,
    RatioMetric,
    TimeRangeType,
    WindowFunc,
    WindowMetric,
)


def _metric_text(metric) -> str:
    if isinstance(metric, RatioMetric):
        num = metric.numerator
        den = metric.denominator
        return (
            f"{metric.alias}（{agg_label(num.agg.value)}{field_label(num.field)}"
            f" 除以 {agg_label(den.agg.value)}{field_label(den.field)}）"
        )
    if isinstance(metric, WindowMetric):
        base = metric.base
        if metric.func == WindowFunc.CUMSUM:
            return f"{metric.alias}（累计{agg_label(base.agg.value)}{field_label(base.field)}）"
        return (
            f"{metric.alias}（近 {metric.window_size} 日移动平均"
            f"{agg_label(base.agg.value)}{field_label(base.field)}）"
        )
    return f"{metric.alias}（{agg_label(metric.agg.value)}{field_label(metric.field)}）"


def _dimension_text(dim) -> str:
    alias = dim.alias or dim.field
    return field_label(alias)


def _filter_text(f) -> str:
    fld = field_label(f.field)
    op = op_label(f.operator.value)
    val = value_label(f.field, f.value)
    if f.operator.value == "between":
        lo, hi = f.value
        return f"{fld} {op} {value_label(f.field, lo)} 和 {value_label(f.field, hi)} 之间"
    return f"{fld} {op} {val}"


def _time_text(tf) -> str:
    if tf is None:
        return ""
    parts: list[str] = []
    if tf.range_type == TimeRangeType.ABSOLUTE and tf.absolute:
        parts.append(f"时间 {tf.absolute.start} 至 {tf.absolute.end}")
    elif tf.relative:
        r = tf.relative
        parts.append(f"最近 {r.amount} {r.unit.value}")
    if tf.comparison.value != "none":
        parts.append("同比" if tf.comparison.value == "yoy" else "环比")
    return "；".join(parts)


def explain(dsl: QueryDSL) -> str:
    """把 QueryDSL 翻译成一句中文业务解释。"""
    clauses: list[str] = []

    # 指标
    metric_txt = "、".join(_metric_text(m) for m in dsl.metrics)
    clauses.append(f"查询指标：{metric_txt}")

    # 维度
    if dsl.dimensions:
        dim_txt = "、".join(_dimension_text(d) for d in dsl.dimensions)
        clauses.append(f"按 {dim_txt} 分组")

    # 过滤
    if dsl.filters:
        filter_txt = "，且 ".join(_filter_text(f) for f in dsl.filters)
        clauses.append(f"筛选条件：{filter_txt}")

    # 时间
    time_txt = _time_text(dsl.time_filter)
    if time_txt:
        clauses.append(time_txt)

    # 排序
    if dsl.order_by:
        ob = dsl.order_by[0]
        direction = "降序" if ob.direction.value == "desc" else "升序"
        clauses.append(f"按 {field_label(ob.field)} {direction}")

    # 分组 Top-N
    if dsl.top_n is not None:
        tn = dsl.top_n
        parts_txt = "、".join(field_label(p) for p in tn.partition_by)
        clauses.append(f"每个 {parts_txt} 取前 {tn.n} 条")

    # 日期补零
    if dsl.fill_gaps:
        clauses.append("缺失日期补零")

    clauses.append(f"最多返回 {dsl.limit} 条")
    return "，".join(clauses) + "。"
