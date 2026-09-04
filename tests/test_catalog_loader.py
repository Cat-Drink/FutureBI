"""语义目录数据驱动（P0-2）单元测试：物理元数据 + 配置覆写构建目录。"""

from __future__ import annotations

import json

import pytest

from compiler.sql_compiler import CompileError, compile_sql
from semantic import catalog
from semantic.catalog_loader import (
    build_catalog,
    refresh_catalog,
    reset_defaults,
)
from semantic.dsl_schema import AggFunc, AggregateMetric, Dimension, QueryDSL


def _overlay(tmp_path, extra_fields=None, extra_join=None) -> str:
    """写一份覆写配置，可注入额外字段/连接声明。"""
    base = {
        "fact_table": "fact_orders",
        "fact_tables": ["fact_orders", "fact_refunds"],
        "aliases": {"fact_orders": "f", "dim_user": "u", "dim_product": "p"},
        "fields": {
            "order_id": {"table": "fact_orders", "column": "order_id", "dtype": "int"},
            "order_amount": {"table": "fact_orders", "column": "order_amount", "dtype": "float"},
            "order_time": {"table": "fact_orders", "column": "order_time", "dtype": "timestamp"},
            "province": {"table": "dim_user", "column": "province", "dtype": "str"},
            "category": {"table": "dim_product", "column": "category", "dtype": "str"},
        },
        "join_rules": {
            "dim_user": {"type": "inner", "on": [["user_id", "user_id"]]},
            "dim_product": {"type": "inner", "on": [["product_id", "product_id"]]},
        },
        "fact_join_rules": {"fact_refunds": {"type": "left", "on": [["order_id", "order_id"]]}},
    }
    if extra_fields:
        base["fields"].update(extra_fields)
    if extra_join:
        base["join_rules"].update(extra_join)
    p = tmp_path / "semantic.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_build_catalog_from_conn_without_overlay(conn):
    """无覆写时：目录与库内物理列一致（默认字段全部存在于 information_schema）。"""
    cat = build_catalog(conn=conn)
    assert cat.fact_table == "fact_orders"
    assert "order_amount" in cat.columns
    assert cat.columns["order_amount"].dtype == "float"
    assert cat.join_rules["dim_user"].join_type == "inner"
    assert cat.fact_join_rules["fact_refunds"].join_type == "left"


def test_overlay_adds_new_field_and_compiles(conn, tmp_path):
    """P0-2 核心：新增逻辑字段只需改配置（映射到库内既有列），即可被编译器引用。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={
            "gross_amount": {"table": "fact_orders", "column": "order_amount", "dtype": "float"}
        },
    )
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        assert "gross_amount" in catalog.COLUMNS
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="gross_amount", agg=AggFunc.SUM, alias="gmv")],
        )
        sql = compile_sql(dsl)
        assert "SUM(f.order_amount)" in sql
    finally:
        reset_defaults()


def test_overlay_join_rule_renders_structured(conn, tmp_path):
    """连接声明改为结构化（join type + 字段对），渲染出受控 JOIN。"""
    overlay = _overlay(tmp_path)
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        dsl = QueryDSL(
            metrics=[AggregateMetric(field="order_amount", agg=AggFunc.SUM, alias="gmv")],
            dimensions=[Dimension(field="province")],
        )
        sql = compile_sql(dsl)
        assert "JOIN dim_user u ON u.user_id = f.user_id" in sql
    finally:
        reset_defaults()


def test_overlay_missing_column_rejected(conn, tmp_path):
    """覆写引用库中不存在的列 -> 拒绝（fail-closed，不静默丢弃）。"""
    overlay = _overlay(
        tmp_path,
        extra_fields={"ghost": {"table": "fact_orders", "column": "no_such_col", "dtype": "str"}},
    )
    with pytest.raises(ValueError, match="no_such_col"):
        build_catalog(conn=conn, overlay_path=overlay)


def test_overlay_missing_table_rejected(conn, tmp_path):
    overlay = _overlay(
        tmp_path,
        extra_fields={"x": {"table": "no_such_table", "column": "c", "dtype": "str"}},
    )
    with pytest.raises(ValueError, match="no_such_table"):
        build_catalog(conn=conn, overlay_path=overlay)


def test_refresh_mutates_globals_and_reset_restores(conn, tmp_path):
    """refresh 安装到 semantic.catalog 全局；reset_defaults 恢复内置默认（测试隔离）。"""
    before = set(catalog.COLUMNS)
    overlay = _overlay(tmp_path)  # 更小字段集
    refresh_catalog(conn=conn, overlay_path=overlay)
    assert set(catalog.COLUMNS) < before  # 覆写字段集更小
    reset_defaults()
    assert set(catalog.COLUMNS) == before


def test_unknown_field_still_rejected_after_refresh(conn, tmp_path):
    """刷新后未登记的字段依然被编译器拒绝（目录即白名单）。"""
    overlay = _overlay(tmp_path)
    refresh_catalog(conn=conn, overlay_path=overlay)
    try:
        dsl = QueryDSL(metrics=[AggregateMetric(field="nope", agg=AggFunc.SUM, alias="x")])
        with pytest.raises(CompileError, match="nope"):
            compile_sql(dsl)
    finally:
        reset_defaults()
