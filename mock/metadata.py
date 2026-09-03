"""数据字典：每张表字段的中文业务注释。

这份元数据既是给开发者的文档，也会被 init_duckdb.py 写入 DuckDB 的
_field_metadata 表，供语义层 / Agent 后续消费。
"""
from __future__ import annotations

FIELD_METADATA: dict[str, dict[str, str]] = {
    "dim_user": {
        "user_id": "用户ID，主键",
        "province": "用户所在省份",
        "gender": "性别（M=男 / F=女）",
        "register_time": "注册时间",
    },
    "dim_product": {
        "product_id": "商品ID，主键",
        "category": "商品类目（数码/家电/服饰/美妆/食品/家居）",
        "brand": "品牌名称",
        "unit_price": "商品单价（元）",
    },
    "fact_orders": {
        "order_id": "订单ID，主键",
        "user_id": "下单用户ID，关联 dim_user.user_id",
        "product_id": "商品ID，关联 dim_product.product_id",
        "order_amount": "实付金额（元），= 单价*数量 - 优惠金额",
        "discount_amount": "优惠金额（元）",
        "pay_status": "支付状态（SUCCESS=成功 / CANCELLED=取消）",
        "order_time": "下单时间（分布在锚点日期前约 400 天（>1 年，支撑同比/环比））",
    },
}

# 表级注释
TABLE_METADATA: dict[str, str] = {
    "dim_user": "用户维度表",
    "dim_product": "商品维度表",
    "fact_orders": "订单事实表（星型模型中心）",
}
