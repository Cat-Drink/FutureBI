"""意图路由：显式三分类。

把用户输入分类为三种意图之一：
- TEXT2SQL：数据分析查询，进入 NL -> DSL -> SQL 链路；
- RAG：询问指标口径 / 定义 / 计算方法，进入口径文档检索；
- CHITCHAT：与数据分析无关的闲聊 / 寒暄，直接礼貌拒绝。

分类为确定性规则（离线可运行、可单测），是 LLM 之前的第一道闸门。
"""

from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    TEXT2SQL = "text2sql"
    RAG = "rag"
    CHITCHAT = "chitchat"


# 口径 / 定义类问题触发词（命中即走 RAG 检索）。
_RAG_PATTERNS = (
    "口径",
    "怎么算",
    "如何算",
    "如何计算",
    "怎么计算",
    "计算方式",
    "计算公式",
    "是什么意思",
    "什么意思",
    "定义",
    "怎么定义",
    "如何定义",
    "指标解释",
    "解释一下",
    "什么口径",
)

# 闲聊 / 寒暄 / 越界话题触发词（命中即拒绝）。
_CHITCHAT_PATTERNS = (
    "你好",
    "您好",
    "早上好",
    "晚上好",
    "下午好",
    "谢谢",
    "再见",
    "哈喽",
    "嗨",
    "天气",
    "讲个笑话",
    "笑话",
    "今天星期几",
    "现在几点",
    "你是谁",
    "你能做什么",
    "你会什么",
    "写首诗",
    "唱首歌",
    "帮我写",
    "给我写",
    "翻译",
    "讲个故事",
)


def classify_intent(query: str) -> Intent:
    """把 query 分类为 TEXT2SQL / RAG / CHITCHAT 之一。"""
    q = query.strip().lower()
    if any(p in q for p in _RAG_PATTERNS):
        return Intent.RAG
    if any(p in q for p in _CHITCHAT_PATTERNS):
        return Intent.CHITCHAT
    return Intent.TEXT2SQL
