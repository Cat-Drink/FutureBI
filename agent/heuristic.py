"""确定性启发式 NL2DSL（离线兜底实现）。

不依赖 LLM，用规则把 golden 覆盖的高频问法映射为 QueryDSL。用途：
1. 无 API Key 时让整条链路离线可运行、可单测、可评测；
2. 作为 LLM 输出的独立对照（cross-check）。

超出规则范围的提问会抛 PipelineError（拒绝而非猜测），与 LLM 路径行为一致。
"""
from __future__ import annotations

import re
from typing import Any

from agent.errors import PipelineError
from config import settings
from semantic.dsl_schema import QueryDSL

PROVINCES = ["广东", "浙江", "江苏", "北京", "上海", "四川", "湖北", "山东"]
CATEGORIES = ["数码", "家电", "服饰", "美妆", "食品", "家居"]


class DeterministicNL2DSL:
    """关键词规则版的 NL -> DSL（覆盖 golden 高频场景）。"""

    def run(self, query: str) -> QueryDSL:
        q = query.strip()
        try:
            dsl: dict[str, Any] = {
                "metrics": self._metrics(q),
                "dimensions": self._dimensions(q),
                "filters": self._filters(q),
                "order_by": self._order_by(q),
                "limit": self._limit(q),
            }
            time_filter = self._time_filter(q)
            if time_filter:
                dsl["time_filter"] = time_filter
            comparison = self._comparison(q)
            if comparison:
                dsl["time_filter"]["comparison"] = comparison
            return QueryDSL.model_validate(dsl)
        except PipelineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PipelineError(f"无法解析提问: {query!r} ({exc})") from exc

    # ------------------------------------------------------------------ #
    # 指标
    # ------------------------------------------------------------------ #
    def _metrics(self, q: str) -> list[dict[str, Any]]:
        if "arpu" in q.lower() or "人均消费" in q:
            return [
                {
                    "kind": "ratio",
                    "numerator": {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"},
                    "denominator": {"kind": "aggregate", "field": "user_id", "agg": "count_distinct", "alias": "active_users"},
                    "alias": "arpu",
                }
            ]
        ql = q.lower()
        if any(k in ql for k in ("gmv", "销售额", "成交额", "成交金额", "总销售")):
            return [{"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}]
        if any(k in q for k in ("去重用户", "活跃用户")):
            return [{"kind": "aggregate", "field": "user_id", "agg": "count_distinct", "alias": "active_users"}]
        if any(k in q for k in ("订单总数", "订单数", "订单量")):
            return [{"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_count"}]
        if "客单价" in q:
            return [{"kind": "aggregate", "field": "order_amount", "agg": "avg", "alias": "avg_order_amount"}]
        raise PipelineError("无法识别指标（需要 GMV/订单数/去重用户/ARPU/客单价 之一）")

    # ------------------------------------------------------------------ #
    # 维度
    # ------------------------------------------------------------------ #
    def _dimensions(self, q: str) -> list[dict[str, Any]]:
        dims: list[dict[str, str]] = []
        if any(k in q for k in ("每日", "按天", "每天", "趋势")):
            dims.append({"field": "order_time"})
        if any(k in q for k in ("各品类", "按品类", "分品类", "品类分布")):
            dims.append({"field": "category"})
        if "品牌" in q:
            dims.append({"field": "brand"})
        if any(k in q for k in ("各省", "按省份", "分省", "省份分布")):
            dims.append({"field": "province"})
        if "支付状态" in q:
            dims.append({"field": "pay_status"})
        return dims

    # ------------------------------------------------------------------ #
    # 过滤
    # ------------------------------------------------------------------ #
    def _filters(self, q: str) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []

        # 支付口径：成功/成交
        if any(k in q for k in ("成功", "成交")):
            filters.append({"field": "pay_status", "operator": "eq", "value": "SUCCESS"})

        # 省份（单个或多个 -> in）
        provinces = [p for p in PROVINCES if p in q]
        if provinces:
            if len(provinces) == 1:
                filters.append({"field": "province", "operator": "eq", "value": provinces[0]})
            else:
                filters.append({"field": "province", "operator": "in", "value": provinces})

        # 类目
        cats = [c for c in CATEGORIES if c in q]
        if cats:
            filters.append({"field": "category", "operator": "eq", "value": cats[0]})

        # 数值区间：金额A到B元
        m = re.search(r"金额?\s*(\d+)\s*到\s*(\d+)\s*元", q)
        if m:
            filters.append(
                {"field": "order_amount", "operator": "between", "value": [int(m.group(1)), int(m.group(2))]}
            )
        return filters

    # ------------------------------------------------------------------ #
    # 时间
    # ------------------------------------------------------------------ #
    def _time_filter(self, q: str) -> dict[str, Any] | None:
        # 绝对：2024年6月 / 2024年
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", q)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
            return {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {
                    "start": f"{year:04d}-{month:02d}-01",
                    "end": f"{end_year:04d}-{end_month:02d}-01",
                },
            }
        m = re.search(r"(\d{4})\s*年", q)
        if m:
            year = int(m.group(1))
            return {
                "granularity": "day",
                "range_type": "absolute",
                "absolute": {"start": f"{year:04d}-01-01", "end": f"{year + 1:04d}-01-01"},
            }

        # 相对：上个月 / 过去N天
        if "上个月" in q or "上月" in q:
            return {
                "granularity": "month",
                "range_type": "relative",
                "relative": {"amount": 1, "unit": "month", "mode": "calendar"},
                "reference_date": settings.AS_OF_DATE.isoformat(),
            }
        m = re.search(r"过去\s*(\d+)\s*天", q)
        if m:
            return {
                "granularity": "day",
                "range_type": "relative",
                "relative": {"amount": int(m.group(1)), "unit": "day", "mode": "trailing"},
                "reference_date": settings.AS_OF_DATE.isoformat(),
            }
        return None

    # ------------------------------------------------------------------ #
    # 对比（同比/环比）
    # ------------------------------------------------------------------ #
    def _comparison(self, q: str) -> str | None:
        ql = q.lower()
        if "同比" in q or "yoy" in ql:
            return "yoy"
        if "环比" in q or "mom" in ql:
            return "mom"
        return None

    # ------------------------------------------------------------------ #
    # 排序 / 截断
    # ------------------------------------------------------------------ #
    def _order_by(self, q: str) -> list[dict[str, Any]]:
        # 趋势 -> 按时间升序
        if any(k in q for k in ("每日", "按天", "每天", "趋势")):
            return [{"field": "order_time", "direction": "asc"}]
        # 最高/前N -> 按主指标降序
        if any(k in q for k in ("最高", "排名", "前")):
            return [{"field": self._primary_alias(q), "direction": "desc"}]
        # 有维度且非"分布"型 -> 按主指标降序（可控默认；"分布"视为不排序的清单型问题）
        if self._dimensions(q) and "分布" not in q:
            return [{"field": self._primary_alias(q), "direction": "desc"}]
        return []

    def _primary_alias(self, q: str) -> str:
        m = self._metrics(q)
        if len(m) == 1 and m[0]["kind"] == "aggregate":
            return m[0]["alias"]
        return "gmv"

    def _limit(self, q: str) -> int:
        m = re.search(r"前\s*(\d+)\s*个", q)
        return int(m.group(1)) if m else 100
