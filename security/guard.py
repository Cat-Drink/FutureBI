"""安全守卫：对 QueryDSL 施加主体策略。

流程：
1. 解析主体策略（未登记主体 -> SecurityError）；
2. 表级校验：DSL 引用的物理表必须都在 allowed_tables；
3. 列级校验：DSL 引用的逻辑字段不得在 forbidden_columns；
4. 行级 RLS：把策略的 row_filters 追加进 DSL.filters，返回新 DSL。

守卫是纯函数：不修改入参 DSL，失败抛 SecurityError（拒绝而非放行）。
"""
from __future__ import annotations

from semantic.catalog import COLUMNS
from semantic.dsl_schema import Filter, QueryDSL, RatioMetric
from security.policy import POLICIES, Policy


class SecurityError(RuntimeError):
    """权限校验失败：主体无权访问引用的表/列，或主体未登记。"""


def _referenced_fields(dsl: QueryDSL) -> set[str]:
    """收集 DSL 引用的全部逻辑字段（指标 + 维度 + 过滤）。"""
    fields: set[str] = set()
    for m in dsl.metrics:
        if isinstance(m, RatioMetric):
            fields.add(m.numerator.field)
            fields.add(m.denominator.field)
        else:
            fields.add(m.field)
    for d in dsl.dimensions:
        fields.add(d.field)
    for f in dsl.filters:
        fields.add(f.field)
    return fields


def _referenced_tables(fields: set[str]) -> set[str]:
    """字段 -> 所属物理表（未登记字段跳过，交由编译器兜底报错）。"""
    tables: set[str] = set()
    for f in fields:
        meta = COLUMNS.get(f)
        if meta is not None:
            tables.add(meta.table)
    return tables


def apply_policy(dsl: QueryDSL, principal: str | None) -> QueryDSL:
    """施加主体策略，返回（可能追加了 RLS 过滤的）新 DSL。

    principal 为 None 时不施加任何限制（等价于 admin），保持向后兼容。
    """
    if principal is None:
        return dsl

    policy: Policy | None = POLICIES.get(principal)
    if policy is None:
        raise SecurityError(f"未登记的主体: {principal!r}")

    fields = _referenced_fields(dsl)
    tables = _referenced_tables(fields)

    # 表级校验
    forbidden_tables = tables - policy.allowed_tables
    if forbidden_tables:
        raise SecurityError(
            f"主体 {principal!r} 无权访问表: {sorted(forbidden_tables)}"
        )

    # 列级校验
    forbidden_fields = fields & policy.forbidden_columns
    if forbidden_fields:
        raise SecurityError(
            f"主体 {principal!r} 无权访问字段: {sorted(forbidden_fields)}"
        )

    # 行级 RLS：追加过滤条件
    if policy.row_filters:
        extra = [Filter.model_validate(rf) for rf in policy.row_filters]
        dsl = dsl.model_copy(update={"filters": list(dsl.filters) + extra})

    return dsl

