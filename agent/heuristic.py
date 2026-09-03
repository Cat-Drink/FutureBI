"""确定性启发式 NL2DSL（离线兜底实现）。

不依赖 LLM，用规则把 golden 覆盖的高频问法映射为 QueryDSL。用途：
1. 无 API Key 时让整条链路离线可运行、可单测、可评测；
2. 作为 LLM 输出的独立对照（cross-check）。

超出规则范围的提问会抛 PipelineError（拒绝而非猜测），与 LLM 路径行为一致。

覆盖的语义能力：
- 单/多指标聚合（GMV、订单数、去重用户、客单价、退款金额）；
- 比率指标（退款率、ARPU）；
- 同比/环比（comparison）；
- 窗口函数（累计 cumsum / 移动平均 moving_avg）；
- 日期连续补零（fill_gaps）；
- 分组 Top-N（每省/每品牌/每品类 Top N）。
"""

from __future__ import annotations

import re
from typing import Any

from agent.clarify import undefined_metric_terms
from agent.errors import PipelineError
from config import settings
from security.errors import SecurityError
from security.scope import scoped_fields
from semantic.dsl_schema import QueryDSL, RatioMetric, WindowMetric

PROVINCES = ["广东", "浙江", "江苏", "北京", "上海", "四川", "湖北", "山东"]
CATEGORIES = ["数码", "家电", "服饰", "美妆", "食品", "家居"]


class DeterministicNL2DSL:
    """关键词规则版的 NL -> DSL（覆盖 golden 高频场景）。"""

    def rewrite(self, query: str, dsl: QueryDSL, error: str, attempts: int = 1) -> QueryDSL:
        """确定性兜底无自愈能力：执行/编译失败无法用规则可靠修正，拒绝而非猜测。

        保持与 LLM 路径一致的接口形态，但明确抛错，让上层自愈循环知难而退，
        并把原始引擎报错透传给用户。
        """
        raise PipelineError("确定性兜底不支持 SQL 自愈重写（未配置 LLM）：" + str(error))

    def run(self, query: str, principal: str | None = None) -> QueryDSL:
        """关键词规则 -> QueryDSL（守卫前移：生成完成前按主体过滤字段作用域）。"""
        q = query.strip()
        # 禁止静默回退默认值：未定义业务指标（如"高活用户"/"高活跃用户"）宁可拒绝，
        # 也不近似映射为已有指标（如"活跃用户"）。路由层负责主动反问，此处兜底拒绝。
        undefined = undefined_metric_terms(q)
        if undefined:
            raise PipelineError(
                "检测到未定义业务指标（" + "、".join(undefined) + "），"
                "请先补充其业务口径，无法可靠解析。"
            )
        try:
            top_n = self._top_n(q)
            dims = self._dimensions(q)
            # 分组 Top-N：分区维度排在最前，其余为排名维度
            if top_n:
                partition = set(top_n["partition_by"])
                rank_dims = [d for d in dims if d["field"] not in partition]
                dims = [{"field": p} for p in top_n["partition_by"]] + rank_dims

            dsl: dict[str, Any] = {
                "metrics": self._metrics(q),
                "dimensions": dims,
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
            if self._fill_gaps(q):
                dsl["fill_gaps"] = True
            if top_n:
                dsl["top_n"] = top_n
            parsed = QueryDSL.model_validate(dsl)
        except PipelineError:
            raise
        except SecurityError:
            raise
        except Exception as exc:
            raise PipelineError(f"无法解析提问: {query!r} ({exc})") from exc

        # 守卫前移：生成完成前按主体过滤可用字段；越权字段直接拒绝（SecurityError）。
        self._enforce_scope(parsed, principal)
        return parsed

    @staticmethod
    def _enforce_scope(dsl: QueryDSL, principal: str | None) -> None:
        """校验 DSL 引用的字段全部在主体作用域内；越权字段抛 SecurityError。

        在确定性路径中，这就是"生成前"过滤：候选 DSL 尚未提交/编译即被拒绝，
        而不是生成后靠守卫兜底（apply_policy 仍作为第二道纵深防御）。
        """
        allowed = scoped_fields(principal)
        referenced: set[str] = set()
        for m in dsl.metrics:
            if isinstance(m, RatioMetric):
                referenced.add(m.numerator.field)
                referenced.add(m.denominator.field)
            elif isinstance(m, WindowMetric):
                referenced.add(m.base.field)
            else:
                referenced.add(m.field)
        for d in dsl.dimensions:
            referenced.add(d.field)
        for f in dsl.filters:
            referenced.add(f.field)
        forbidden = referenced - allowed
        if forbidden:
            raise SecurityError(f"主体 {principal!r} 无权访问字段: {sorted(forbidden)}")

    # ------------------------------------------------------------------ #
    # 指标
    # ------------------------------------------------------------------ #
    def _metrics(self, q: str) -> list[dict[str, Any]]:
        ql = q.lower()

        # 窗口指标（累计/移动平均）优先
        wm = self._window_metric(q)
        if wm is not None:
            return [wm]

        metrics: list[dict[str, Any]] = []

        # 比率指标：退款率（早退，独占）
        if "退款率" in q or "退款金额/订单金额" in q:
            return [
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "refund_amount",
                        "agg": "sum",
                        "alias": "refund_amount",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "alias": "refund_rate",
                }
            ]
        if "退款金额" in q or "退款总额" in q:
            metrics.append(
                {
                    "kind": "aggregate",
                    "field": "refund_amount",
                    "agg": "sum",
                    "alias": "refund_amount",
                }
            )
        if "arpu" in ql or "人均消费" in q:
            metrics.append(
                {
                    "kind": "ratio",
                    "numerator": {
                        "kind": "aggregate",
                        "field": "order_amount",
                        "agg": "sum",
                        "alias": "gmv",
                    },
                    "denominator": {
                        "kind": "aggregate",
                        "field": "user_id",
                        "agg": "count_distinct",
                        "alias": "active_users",
                    },
                    "alias": "arpu",
                }
            )
        if any(k in ql for k in ("gmv", "销售额", "成交额", "成交金额", "总销售")):
            metrics.append(
                {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            )
        if any(k in q for k in ("去重用户", "活跃用户")):
            metrics.append(
                {
                    "kind": "aggregate",
                    "field": "user_id",
                    "agg": "count_distinct",
                    "alias": "active_users",
                }
            )
        if any(k in q for k in ("订单总数", "订单数", "订单量")):
            metrics.append(
                {"kind": "aggregate", "field": "order_id", "agg": "count", "alias": "order_count"}
            )
        if "客单价" in q:
            metrics.append(
                {
                    "kind": "aggregate",
                    "field": "order_amount",
                    "agg": "avg",
                    "alias": "avg_order_amount",
                }
            )

        if not metrics:
            raise PipelineError("无法识别指标（需要 GMV/订单数/去重用户/ARPU/客单价 之一）")
        return metrics

    def _window_metric(self, q: str) -> dict[str, Any] | None:
        """识别窗口指标：累计（cumsum）/ 移动平均（moving_avg）。"""
        is_ma = "移动平均" in q or "滑动平均" in q
        is_cum = "累计" in q
        if not (is_ma or is_cum):
            return None

        if "订单数" in q or "订单量" in q:
            base = {
                "kind": "aggregate",
                "field": "order_id",
                "agg": "count",
                "alias": "order_count",
            }
            stem = "order_count"
        else:
            base = {"kind": "aggregate", "field": "order_amount", "agg": "sum", "alias": "gmv"}
            stem = "gmv"

        if is_ma:
            m = re.search(r"(\d+)\s*日", q)
            size = int(m.group(1)) if m else 7
            return {
                "kind": "window",
                "base": base,
                "func": "moving_avg",
                "window_size": size,
                "alias": f"ma{size}_{stem}",
            }
        return {"kind": "window", "base": base, "func": "cumsum", "alias": f"cum_{stem}"}

    # ------------------------------------------------------------------ #
    # 维度
    # ------------------------------------------------------------------ #
    def _dimensions(self, q: str) -> list[dict[str, str]]:
        dims: list[dict[str, str]] = []
        seen: set[str] = set()

        def add(field: str) -> None:
            if field not in seen:
                dims.append({"field": field})
                seen.add(field)

        if any(
            k in q
            for k in (
                "每日",
                "按天",
                "每天",
                "趋势",
                "累计",
                "移动平均",
                "滑动平均",
                "补零",
                "补齐",
            )
        ):
            add("order_time")
        if any(k in q for k in ("各品类", "按品类", "分品类", "品类分布", "每品类", "品类")):
            add("category")
        if "品牌" in q:
            add("brand")
        if any(k in q for k in ("各省", "按省份", "分省", "省份分布", "每省")):
            add("province")
        if "支付状态" in q:
            add("pay_status")
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
                {
                    "field": "order_amount",
                    "operator": "between",
                    "value": [int(m.group(1)), int(m.group(2))],
                }
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
    # 补零 / 分组 Top-N
    # ------------------------------------------------------------------ #
    def _fill_gaps(self, q: str) -> bool:
        return any(k in q for k in ("补零", "补齐", "补全"))

    def _top_n(self, q: str) -> dict[str, Any] | None:
        ql = q.lower()
        n = None
        m = re.search(r"top\s*(\d+)", ql)
        if not m:
            m = re.search(r"前\s*(\d+)\s*[个名]", q)
        if not m:
            return None
        n = int(m.group(1))

        partition: list[str] = []
        if any(k in q for k in ("每省", "各省", "按省")):
            partition.append("province")
        elif any(k in q for k in ("每品牌", "各品牌")):
            partition.append("brand")
        elif any(k in q for k in ("每品类", "各品类")):
            partition.append("category")
        if not partition:
            return None
        return {
            "n": n,
            "partition_by": partition,
            "order_by": [{"field": self._primary_alias(q), "direction": "desc"}],
        }

    # ------------------------------------------------------------------ #
    # 排序 / 截断
    # ------------------------------------------------------------------ #
    def _order_by(self, q: str) -> list[dict[str, Any]]:
        # 趋势/窗口/补零 -> 按时间升序
        if any(
            k in q
            for k in (
                "每日",
                "按天",
                "每天",
                "趋势",
                "累计",
                "移动平均",
                "滑动平均",
                "补零",
                "补齐",
            )
        ):
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
