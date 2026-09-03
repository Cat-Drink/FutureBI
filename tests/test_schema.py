"""DSL 数据契约单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from semantic.dsl_schema import (
    QueryDSL,
    TimeFilter,
)


def _base_dsl(**overrides) -> dict:
    dsl = {
        "metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}],
        "filters": [],
        "order_by": [],
        "limit": 100,
    }
    dsl.update(overrides)
    return dsl


def test_minimal_dsl_parses():
    dsl = QueryDSL.model_validate(_base_dsl())
    assert dsl.limit == 100
    assert dsl.metrics[0].alias == "gmv"


def test_default_limit_is_100():
    dsl = QueryDSL.model_validate(
        {"metrics": [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]}
    )
    assert dsl.limit == 100


def test_extra_field_is_forbidden():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(_base_dsl(sneaky="injection"))


def test_between_requires_two_values():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(filters=[{"field": "order_amount", "operator": "between", "value": [1]}])
        )


def test_in_requires_nonempty_list():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(filters=[{"field": "province", "operator": "in", "value": []}])
        )


def test_relative_time_requires_relative_object():
    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            _base_dsl(time_filter={"granularity": "day", "range_type": "relative"})
        )


def test_comparison_marker_supported_in_schema():
    tf = TimeFilter.model_validate(
        {
            "granularity": "month",
            "range_type": "absolute",
            "absolute": {"start": "2024-05-01", "end": "2024-06-01"},
            "comparison": "mom",
        }
    )
    assert tf.comparison.value == "mom"
