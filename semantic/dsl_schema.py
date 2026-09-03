"""语义 DSL 数据契约（Pydantic V2）。

本模块是 "自然语言 -> 结构化 DSL -> 确定性 SQL" 链路中的唯一事实来源
（Single Source of Truth）。Agent 只能产出符合本契约的 JSON，编译器只接受本契约
对象，从而在结构上杜绝 SQL 注入与任意 Join。

约束设计要点：
- 所有模型 extra="forbid"，非法/未知字段直接报错；
- 操作符、聚合函数、时间粒度、排序方向均为受限枚举；
- TimeFilter 支持相对/绝对时间跨度与同比/环比标记（comparison）；
- Metric 通过 discriminated union 区分聚合指标与比率指标（如 ARPU）。
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Union, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --------------------------------------------------------------------------- #
# 时间相关枚举
# --------------------------------------------------------------------------- #
class Granularity(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Comparison(str, Enum):
    """同比/环比标记。当前编译器仅支持 none，其余二期实现。"""

    NONE = "none"
    YOY = "yoy"  # 同比
    MOM = "mom"  # 环比


class TimeRangeType(str, Enum):
    RELATIVE = "relative"
    ABSOLUTE = "absolute"


class RelativeUnit(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class RelativeMode(str, Enum):
    """相对窗口语义。

    - trailing：相对锚点向前滚动 N 个时间单位，窗口为 [锚点-N单位, 锚点)。
    - calendar：自然日历周期，例如 "上个月" 为整个上一个自然月。
    """

    TRAILING = "trailing"
    CALENDAR = "calendar"


class RelativeTime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0, description="时间跨度数值，必须为正整数")
    unit: RelativeUnit = RelativeUnit.DAY
    mode: RelativeMode = RelativeMode.TRAILING


class AbsoluteTime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def _check_order(self) -> "AbsoluteTime":
        if self.start >= self.end:
            raise ValueError("absolute.start 必须严格早于 absolute.end")
        return self


class TimeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granularity: Granularity = Granularity.DAY
    range_type: TimeRangeType = TimeRangeType.RELATIVE
    relative: RelativeTime | None = None
    absolute: AbsoluteTime | None = None
    comparison: Comparison = Comparison.NONE
    reference_date: date | None = Field(
        default=None,
        description="相对时间窗口的锚点日期；为空时回退到 config.AS_OF_DATE",
    )

    @model_validator(mode="after")
    def _check_range(self) -> "TimeFilter":
        if self.range_type == TimeRangeType.RELATIVE and self.relative is None:
            raise ValueError("range_type=relative 时必须提供 relative 对象")
        if self.range_type == TimeRangeType.ABSOLUTE and self.absolute is None:
            raise ValueError("range_type=absolute 时必须提供 absolute 对象")
        return self


# --------------------------------------------------------------------------- #
# 指标相关
# --------------------------------------------------------------------------- #
class AggFunc(str, Enum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class AggregateMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["aggregate"] = "aggregate"
    field: str = Field(min_length=1, description="聚合字段（语义目录中的逻辑字段名）")
    agg: AggFunc = AggFunc.SUM
    alias: str = Field(min_length=1, description="输出列别名")


class RatioMetric(BaseModel):
    """比率指标，如 ARPU = 总GMV / 活跃用户数。

    numerator / denominator 必须是聚合指标，不允许嵌套比率，保证可确定性编译。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ratio"] = "ratio"
    numerator: AggregateMetric
    denominator: AggregateMetric
    alias: str = Field(min_length=1)


Metric = Annotated[
    Union[AggregateMetric, RatioMetric], Field(discriminator="kind")
]


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    alias: str | None = None


# --------------------------------------------------------------------------- #
# 过滤 / 排序
# --------------------------------------------------------------------------- #
class FilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"


FilterValue = Union[str, int, float, bool, list[Union[str, int, float, bool]]]


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    operator: FilterOperator
    value: FilterValue

    @model_validator(mode="after")
    def _check_value(self) -> "Filter":
        if self.operator in (FilterOperator.IN, FilterOperator.BETWEEN):
            if not isinstance(self.value, list) or len(self.value) == 0:
                raise ValueError(f"{self.operator.value} 操作要求 value 为非空列表")
            if self.operator == FilterOperator.BETWEEN and len(self.value) != 2:
                raise ValueError("between 操作要求 value 为两个元素的列表 [low, high]")
        else:
            if isinstance(self.value, list):
                raise ValueError(f"{self.operator.value} 操作要求 value 为标量")
        return self


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    direction: SortDirection = SortDirection.DESC


# --------------------------------------------------------------------------- #
# 顶层 DSL
# --------------------------------------------------------------------------- #
class QueryDSL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[Metric] = Field(min_length=1)
    dimensions: list[Dimension] = Field(default_factory=list)
    time_filter: TimeFilter | None = None
    filters: list[Filter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10000)
