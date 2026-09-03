"""语义目录：逻辑字段 -> 物理表/列 的受控映射。

这是"杜绝随意 Join / SQL 注入"的关键防线：编译器只允许引用本目录登记的字段，
表连接关系也只由本目录声明，禁止任意 Join。

多事实表模型：
- FACT_TABLE 是主事实表（查询锚点，FROM 主表）；
- 第二事实表（如 fact_refunds）通过 FACT_JOIN_RULES 受控连接，且与主事实表
  在业务上保证 1:1（每订单至多一条退款），避免一对多扇出放大聚合结果。
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
    # fact_orders（主事实表）
    "order_id": FieldMeta("fact_orders", "order_id", "int"),
    "user_id": FieldMeta("fact_orders", "user_id", "int"),
    "product_id": FieldMeta("fact_orders", "product_id", "int"),
    "order_amount": FieldMeta("fact_orders", "order_amount", "float"),
    "discount_amount": FieldMeta("fact_orders", "discount_amount", "float"),
    "pay_status": FieldMeta("fact_orders", "pay_status", "str"),
    "order_time": FieldMeta("fact_orders", "order_time", "timestamp"),
    # fact_refunds（第二事实表：退款）
    "refund_id": FieldMeta("fact_refunds", "refund_id", "int"),
    "refund_amount": FieldMeta("fact_refunds", "refund_amount", "float"),
    "refund_time": FieldMeta("fact_refunds", "refund_time", "timestamp"),
    "refund_status": FieldMeta("fact_refunds", "refund_status", "str"),
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
    "fact_refunds": "r",
    "dim_user": "u",
    "dim_product": "p",
}

# 主事实表（查询锚点，FROM 主表）
FACT_TABLE: str = "fact_orders"

# 全部事实表（用于校验/文档）
FACT_TABLES: tuple[str, ...] = ("fact_orders", "fact_refunds")

# 受控连接规则：只允许从主事实表星型连接维度表
JOIN_RULES: dict[str, str] = {
    "dim_user": "JOIN dim_user u ON u.user_id = f.user_id",
    "dim_product": "JOIN dim_product p ON p.product_id = f.product_id",
}

# 第二事实表 -> 主事实表 的受控连接（LEFT JOIN，业务上 1:1，无扇出）
FACT_JOIN_RULES: dict[str, str] = {
    "fact_refunds": "LEFT JOIN fact_refunds r ON r.order_id = f.order_id",
}
