"""Agent 路由包：意图分类与调度分发。

本模块提供生产级意图识别与路由决策中心，将系统从"线性固定执行管线"升级为
"具备自主路径决策能力的 Agent"。

向后兼容层（legacy）：保留旧的 Action / route_query 接口，供既有调用方无感迁移。
新增五分类意图体系（IntentType）+ RouteDecision 契约 + IntentRouter 核心逻辑。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 新意图体系：五分类意图类型
# --------------------------------------------------------------------------- #
from agent.router.intent_router import (
    CHITCHAT,
    CLARIFY,
    DATA_QUERY,
    GLOSSARY_EXPLAIN,
    INTENT_TYPE_VALUES,
    ROUTING_LATENCY_MS,
    SYSTEM_ACTION,
    IntentRouter,
    IntentType,
    RouteDecision,
    route_decision,
)

# --------------------------------------------------------------------------- #
# 向后兼容层：直接转发旧模块导出（保持现有导入路径不变）
# --------------------------------------------------------------------------- #
from agent.router.legacy import (
    Action,
    Clarification,
    GlossaryDoc,
    RouteResult,
    detect_clarifications,
    retrieve,
    route_query,
    undefined_metric_terms,
)

__all__ = [
    # new
    "CHITCHAT",
    "CLARIFY",
    "DATA_QUERY",
    "GLOSSARY_EXPLAIN",
    "INTENT_TYPE_VALUES",
    "ROUTING_LATENCY_MS",
    "SYSTEM_ACTION",
    # legacy
    "Action",
    "Clarification",
    "GlossaryDoc",
    "IntentRouter",
    "IntentType",
    "RouteDecision",
    "RouteResult",
    "detect_clarifications",
    "retrieve",
    "route_decision",
    "route_query",
    "undefined_metric_terms",
]
