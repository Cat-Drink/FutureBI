"""SQL 编译器单元测试。"""
from __future__ import annotations

import pytest

from compiler.sql_compiler import CompileError, compile_sql
from semantic.dsl_schema import QueryDSL


def test_single_metric_no_dimension(conn):
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert "SUM(f.order_amount) AS gmv" in sql
    row = conn.execute(sql).fetchone()
    assert row[0] > 0


def test_dimension_triggers_group_by_and_join(conn):
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
            "dimensions": [{"field": "category"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert "JOIN dim_product p" in sql
    assert "GROUP BY p.category" in sql
    rows = conn.execute(sql).fetchall()
    assert len(rows) > 0


def test_ratio_metric(conn):
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{
                "kind": "ratio",
                "numerator": {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                "denominator": {"kind": "aggregate", "field": "user_id", "agg": "count_distinct", "alias": "active_users"},
                "alias": "arpu",
            }],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
        }
    )
    sql = compile_sql(dsl)
    assert "(SUM(f.order_amount)) / (COUNT(DISTINCT f.user_id)) AS arpu" in sql
    assert conn.execute(sql).fetchone()[0] > 0


def test_unregistered_field_rejected():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "hacked_column", "agg": "sum", "alias": "x"}],
        }
    )
    with pytest.raises(CompileError):
        compile_sql(dsl)


def test_string_literal_escaped():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
            "filters": [{"field": "province", "operator": "eq", "value": "O'Reilly"}],
        }
    )
    sql = compile_sql(dsl)
    assert "O''Reilly" in sql

def test_comparison_mom_compiles_cte(conn):
    """环比：应生成 cur/prev 双窗口并输出增长率列。"""
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
            "filters": [{"field": "pay_status", "operator": "eq", "value": "SUCCESS"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "mom",
            },
        }
    )
    sql = compile_sql(dsl)
    assert "WITH cur AS (" in sql
    assert "gmv_prev" in sql
    assert "gmv_mom" in sql
    row = conn.execute(sql).fetchone()
    cur, prev, mom = row
    assert cur > 0 and prev > 0
    assert abs(mom - (cur - prev) / prev) < 1e-9


def test_comparison_yoy_uses_year_shift():
    dsl = QueryDSL.model_validate(
        {
            "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
            "time_filter": {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": "2024-06-01", "end": "2024-07-01"},
                "comparison": "yoy",
            },
        }
    )
    sql = compile_sql(dsl)
    assert "gmv_yoy" in sql
    assert "2023-06-01 00:00:00" in sql
