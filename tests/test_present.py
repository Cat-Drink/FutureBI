"""展示层单元测试：DSL -> 解释 + 可视化推荐。"""

from __future__ import annotations

from present.explain import explain
from present.viz import recommend_viz, viz_config
from semantic.dsl_schema import QueryDSL


def _dsl(**over):
    base = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
    }
    base.update(over)
    return QueryDSL.model_validate(base)


# --------------------------------------------------------------------------- #
# explain
# --------------------------------------------------------------------------- #
def test_explain_single_metric():
    dsl = _dsl(
        filters=[{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
    )
    text = explain(dsl)
    assert "gmv" in text
    assert "求和订单金额" in text
    assert "支付状态 等于 成功" in text


def test_explain_dimension_and_time():
    dsl = _dsl(
        dimensions=[{"field": "category"}],
        time_filter={
            "granularity": "day",
            "range_type": "absolute",
            "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
        },
    )
    text = explain(dsl)
    assert "按 类目 分组" in text
    assert "2024-06-01 至 2024-07-01" in text


def test_explain_ratio_metric():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "user_id",
                        "agg": "count_distinct",
                        "alias": "active_users",
                    },
                    "alias": "arpu",
                }
            ],
        }
    )
    text = explain(dsl)
    assert "arpu" in text
    assert "除以" in text


def test_explain_comparison():
    dsl = _dsl(
        time_filter={
            "granularity": "day",
            "range_type": "absolute",
            "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
            "comparison": "mom",
        },
    )
    text = explain(dsl)
    assert "环比" in text


# --------------------------------------------------------------------------- #
# viz
# --------------------------------------------------------------------------- #
def test_viz_number():
    dsl = _dsl()
    assert recommend_viz(dsl, ("gmv",), ()) == "number"


def test_viz_line_for_time_trend():
    dsl = _dsl(dimensions=[{"field": "order_time"}])
    assert recommend_viz(dsl, ("order_time", "gmv"), [(1,), (2,)]) == "line"


def test_viz_pie_for_few_categories():
    dsl = _dsl(dimensions=[{"field": "category"}])
    rows = tuple((f"c{i}",) for i in range(5))
    assert recommend_viz(dsl, ("category", "gmv"), rows) == "pie"


def test_viz_bar_for_many_categories():
    dsl = _dsl(dimensions=[{"field": "category"}])
    rows = tuple((f"c{i}",) for i in range(20))
    assert recommend_viz(dsl, ("category", "gmv"), rows) == "bar"


def test_viz_table_for_multi_dimension():
    dsl = _dsl(dimensions=[{"field": "category"}, {"field": "brand"}])
    rows = (("a", "b", 1),)
    assert recommend_viz(dsl, ("category", "brand", "gmv"), rows) == "table"


def test_viz_config_shape():
    dsl = _dsl(dimensions=[{"field": "category"}])
    cfg = viz_config(dsl, ("category", "gmv"), (("a",),))
    assert cfg == {"chart": "pie", "x": "category", "y": "gmv"}
