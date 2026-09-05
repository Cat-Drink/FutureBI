"""Agent 路由向后兼容层：从旧 agent/router.py 迁移而来，保持既有导入路径不变。

本文件保留 Action / route_query 等既有 API，供 web/service、测试与第三方调用方无感迁移到新五分类体系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent.clarify import Clarification, detect_clarifications
from agent.clarify import undefined_metric_terms as undefined_metric_terms
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


def route_query(query: str, principal: str | None = None) -> RouteResult:
    """对 query 做意图路由与语义澄清，返回可执行的下一步动作。

    principal 非 None 时，RAG 检索的口径文档按主体过滤（守卫前移）。
    """
    intent = classify_intent(query)

    if intent == Intent.CHITCHAT:
        return RouteResult(intent=intent, action=Action.CHITCHAT, message=_CHITCHAT_REPLY)

    if intent == Intent.RAG:
        documents = retrieve(query, principal=principal)
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
