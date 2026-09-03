"""可视化推荐：根据 DSL + 结果形状推荐图表类型（确定性规则）。

规则（按优先级）：
1. 无维度、单指标 -> "number"（单值卡片）；
2. 维度含下单时间/退款时间（趋势）-> "line"（折线）；
3. 单维度、单指标 -> "bar"（柱状），类别数 <= 8 可 "pie"（饼图）；
4. 其余 -> "table"（明细表）。
"""

from __future__ import annotations

from semantic.dsl_schema import QueryDSL


def recommend_viz(
    dsl: QueryDSL,
    columns: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
) -> str:
    """返回推荐图表类型：number / line / bar / pie / pivot / table。"""
    dims = [d.alias or d.field for d in dsl.dimensions]
    n_metrics = len(dsl.metrics)
    n_rows = len(rows)

    # 单值卡片
    if not dims and n_metrics == 1 and n_rows <= 1:
        return "number"

    # 时间趋势 -> 折线
    time_dims = {"order_time", "refund_time", "register_time"}
    if any(d in time_dims for d in dims):
        return "line"

    # 单维度单指标 -> 柱状 / 饼图
    if len(dims) == 1 and n_metrics == 1:
        if n_rows <= 8:
            return "pie"
        return "bar"

    # 多维或单维多指标 -> 透视表（P0 / §4 项5）
    if len(dims) >= 2 or n_metrics >= 2:
        return "pivot"

    # 其余 -> 明细表
    return "table"


def viz_config(
    dsl: QueryDSL,
    columns: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
) -> dict:
    """返回可视化配置（类型 + x/y 轴字段名），供前端直接消费。"""
    chart = recommend_viz(dsl, columns, rows)
    dims = [d.alias or d.field for d in dsl.dimensions]
    metrics = [m.alias for m in dsl.metrics]
    return {
        "chart": chart,
        "x": dims[0] if dims else None,
        "y": metrics[0] if metrics else None,
    }
