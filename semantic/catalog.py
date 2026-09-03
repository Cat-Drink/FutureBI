"""语义目录：逻辑字段 -> 物理表/列 的受控映射。

这是"杜绝随意 Join / SQL 注入"的关键防线：编译器只允许引用本目录登记的字段，
表连接关系也只由本目录声明，禁止任意 Join。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldMeta:
    table: str
    column: str
    dtype: str  # 用于字面量安全转义：str / int / float / bool / timestamp


# 逻辑字段 -> 物理字段
COLUMNS: dict[str, FieldMeta] = {
    # fact_orders
    "order_id": FieldMeta("fact_orders", "order_id", "int"),
    "user_id": FieldMeta("fact_orders", "user_id", "int"),
    "product_id": FieldMeta("fact_orders", "product_id", "int"),
    "order_amount": FieldMeta("fact_orders", "order_amount", "float"),
    "discount_amount": FieldMeta("fact_orders", "discount_amount", "float"),
    "pay_status": FieldMeta("fact_orders", "pay_status", "str"),
    "order_time": FieldMeta("fact_orders", "order_time", "timestamp"),
    # dim_user
    "province": FieldMeta("dim_user", "province", "str"),
    "gender": FieldMeta("dim_user", "gender", "str"),
    "register_time": FieldMeta("dim_user", "register_time", "timestamp"),
    # dim_product
    "category": FieldMeta("dim_product", "category", "str"),
    "brand": FieldMeta("dim_product", "brand", "str"),
    "unit_price": FieldMeta("dim_product", "unit_price", "float"),
}

# 表别名（编译器内部使用）
ALIASES: dict[str, str] = {
    "fact_orders": "f",
    "dim_user": "u",
    "dim_product": "p",
}

# 受控连接规则：只允许从事实表星型连接维度表
FACT_TABLE: str = "fact_orders"

JOIN_RULES: dict[str, str] = {
    "dim_user": "JOIN dim_user u ON u.user_id = f.user_id",
    "dim_product": "JOIN dim_product p ON p.product_id = f.product_id",
}
