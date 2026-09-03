"""意图路由编排：显式三分类 + 语义澄清反问。

route_query 是受控生产入口（在生成 DSL 之前运行）：
1. classify_intent 做三分类：Text2SQL / 口径文档 RAG 检索 / 闲聊拒绝；
2. Text2SQL 且存在缺失时间窗口或未定义业务指标时，返回澄清反问，
   绝不静默回退默认值（默认时间窗口 / 近似指标）；
3. RAG 检索口径文档；闲聊礼貌拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent.clarify import Clarification, detect_clarifications
from agent.glossary import GlossaryDoc
from agent.intent import Intent, classify_intent
from agent.rag import retrieve


class Action(StrEnum):
    TEXT2SQL = "text2sql"
    RAG = "rag"
    CHITCHAT = "chitchat"
    CLARIFY = "clarify"


@dataclass
class RouteResult:
    intent: Intent
    action: Action
    message: str = ""
    clarifications: list[Clarification] = field(default_factory=list)
    documents: list[GlossaryDoc] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent.value,
            "action": self.action.value,
            "message": self.message,
            "clarifications": [c.to_dict() for c in self.clarifications],
            "documents": [d.to_dict() for d in self.documents],
        }


_CHITCHAT_REPLY = "抱歉，我是数据分析助手，只能回答与业务数据相关的问题。"


def route_query(query: str) -> RouteResult:
    """对 query 做意图路由与语义澄清，返回可执行的下一步动作。"""
    intent = classify_intent(query)

    if intent == Intent.CHITCHAT:
        return RouteResult(intent=intent, action=Action.CHITCHAT, message=_CHITCHAT_REPLY)

    if intent == Intent.RAG:
        documents = retrieve(query)
        if documents:
            return RouteResult(
                intent=intent,
                action=Action.RAG,
                documents=documents,
                message="已检索到以下指标口径文档：",
            )
        # 未命中文档：可能是未定义指标的定义询问，转为澄清反问
        clarifications = detect_clarifications(query)
        if clarifications:
            return RouteResult(
                intent=intent,
                action=Action.CLARIFY,
                clarifications=clarifications,
                message="；".join(c.question for c in clarifications),
            )
        return RouteResult(
            intent=intent,
            action=Action.RAG,
            documents=[],
            message="未检索到相关口径文档，请换一种表述或补充指标口径。",
        )

    # TEXT2SQL：先做语义澄清，禁止静默回退默认值
    clarifications = detect_clarifications(query)
    if clarifications:
        return RouteResult(
            intent=intent,
            action=Action.CLARIFY,
            clarifications=clarifications,
            message="；".join(c.question for c in clarifications),
        )
    return RouteResult(intent=intent, action=Action.TEXT2SQL)
