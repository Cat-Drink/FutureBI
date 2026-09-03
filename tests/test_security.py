"""安全守卫单元测试：表级/列级/行级 RLS + 默认无策略。"""
from __future__ import annotations

import pytest

from agent.pipeline import run_pipeline
from compiler.sql_compiler import compile_sql
from security.guard import SecurityError, apply_policy
from semantic.dsl_schema import QueryDSL


def _dsl(**over):
    base = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
    }
    base.update(over)
    return QueryDSL.model_validate(base)


# --------------------------------------------------------------------------- #
# 表级权限
# --------------------------------------------------------------------------- #
def test_restricted_denies_refund_table():
    """受限主体引用退款表字段 -> SecurityError。"""
    dsl = _dsl(
        metrics=[{"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "refund_amount"}],
    )
    with pytest.raises(SecurityError):
        apply_policy(dsl, "restricted")


def test_admin_allows_refund_table():
    dsl = _dsl(
        metrics=[{"kind": "aggregate", "field": "refund_amount", "agg": "sum", "alias": "refund_amount"}],
    )
    out = apply_policy(dsl, "admin")
    assert out == dsl


# --------------------------------------------------------------------------- #
# 列级权限
# --------------------------------------------------------------------------- #
def test_restricted_denies_discount_column():
    dsl = _dsl(
        metrics=[{"kind": "aggregate", "field": "discount_amount", "agg": "sum", "alias": "discount"}],
    )
    with pytest.raises(SecurityError):
        apply_policy(dsl, "restricted")


def test_analyst_allows_discount_column():
    dsl = _dsl(
        metrics=[{"kind": "aggregate", "field": "discount_amount", "agg": "sum", "alias": "discount"}],
    )
    out = apply_policy(dsl, "analyst")
    assert out.metrics[0].alias == "discount"


# --------------------------------------------------------------------------- #
# 行级 RLS
# --------------------------------------------------------------------------- #
def test_rls_injects_province_filter(conn):
    """RLS：受限主体只能看广东，编译 SQL 应含 province IN 且结果只含广东。"""
    dsl = _dsl(
        dimensions=[{"field": "province"}],
    )
    guarded = apply_policy(dsl, "restricted")
    assert len(guarded.filters) == 1
    assert guarded.filters[0].field == "province"
    assert guarded.filters[0].value == ["广东"]

    sql = compile_sql(guarded)
    assert "u.province IN ('广东')" in sql
    rows = conn.execute(sql).fetchall()
    assert all(r[0] == "广东" for r in rows)


def test_rls_analyst_limits_five_provinces(conn):
    dsl = _dsl(dimensions=[{"field": "province"}])
    guarded = apply_policy(dsl, "analyst")
    sql = compile_sql(guarded)
    rows = conn.execute(sql).fetchall()
    provinces = {r[0] for r in rows}
    assert provinces <= {"广东", "浙江", "江苏", "北京", "上海"}


# --------------------------------------------------------------------------- #
# 默认无策略 / 未登记主体
# --------------------------------------------------------------------------- #
def test_none_principal_is_noop():
    dsl = _dsl()
    assert apply_policy(dsl, None) is dsl


def test_unknown_principal_raises():
    dsl = _dsl()
    with pytest.raises(SecurityError):
        apply_policy(dsl, "ghost")


def test_run_pipeline_accepts_principal():
    """run_pipeline(query, principal=...) 端到端：受限主体查询退款 -> 拒绝。"""
    with pytest.raises(SecurityError):
        run_pipeline("各品类成功订单的退款金额是多少？", principal="restricted")

