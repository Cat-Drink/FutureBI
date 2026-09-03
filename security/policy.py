"""权限策略模型与内置策略注册表。

三类权限：
1. 表级（allowed_tables）：主体可查询哪些物理表（默认拒绝）；
2. 列级（forbidden_columns）：主体不可引用的逻辑字段（敏感列，如优惠金额）；
3. 行级 RLS（row_filters）：强制注入的过滤条件（如区域经理只能看本省数据）。

策略为不可变对象，guard 施加策略后返回新 DSL，原 DSL 不受污染。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Policy:
    name: str
    allowed_tables: frozenset[str]
    forbidden_columns: frozenset[str] = frozenset()
    # RLS 过滤，元素为可被 Filter.model_validate 接受的 dict
    row_filters: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.allowed_tables:
            raise ValueError("allowed_tables 不能为空（默认拒绝）")


# 全表集合
ALL_TABLES = frozenset({"fact_orders", "fact_refunds", "dim_user", "dim_product"})


def _province_filter(provinces: list[str]) -> dict[str, Any]:
    return {"field": "province", "operator": "in", "value": provinces}


POLICIES: dict[str, Policy] = {
    # 管理员：全表、无敏感列限制、无 RLS
    "admin": Policy(
        name="admin",
        allowed_tables=ALL_TABLES,
    ),
    # 分析师：全表、无敏感列限制，但只能看 5 个省（RLS）
    "analyst": Policy(
        name="analyst",
        allowed_tables=ALL_TABLES,
        row_filters=(_province_filter(["广东", "浙江", "江苏", "北京", "上海"]),),
    ),
    # 受限运营：不能看退款表，不能看优惠金额/退款金额，只能看广东
    "restricted": Policy(
        name="restricted",
        allowed_tables=frozenset({"fact_orders", "dim_user", "dim_product"}),
        forbidden_columns=frozenset({"discount_amount", "refund_amount"}),
        row_filters=(_province_filter(["广东"]),),
    ),
}
