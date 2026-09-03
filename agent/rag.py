"""口径文档 RAG 检索（零依赖、确定性、可复现）。

对"指标口径 / 定义 / 怎么算"类问题，从 agent.glossary.GLOSSARY 中检索相关口径文档。
检索采用中文友好、可复现的浅层语义匹配（别名命中 + 字符 bigram 重叠打分），
不引入向量库，保持离线可运行、可单测、可评测。
"""

from __future__ import annotations

import re

from agent.glossary import GLOSSARY, GlossaryDoc


def _bigrams(text: str) -> set[str]:
    """把文本切成字符 bigram（中文按相邻二字，英文小写后按相邻字符）。"""
    text = re.sub(r"\s+", "", text.lower())
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def retrieve(query: str, top_k: int = 3) -> list[GlossaryDoc]:
    """检索与 query 最相关的口径文档，按相关度降序返回至多 top_k 条。"""
    q = query.lower()
    q_grams = _bigrams(query)
    scored: list[tuple[int, int, GlossaryDoc]] = []
    for idx, doc in enumerate(GLOSSARY):
        score = 0
        for alias in doc.aliases:
            if alias.lower() in q:
                score += len(alias) * 4
        if doc.title.lower() in q:
            score += 8
        doc_grams = _bigrams(doc.title + " " + doc.definition)
        score += len(q_grams & doc_grams)
        if score > 0:
            scored.append((score, -idx, doc))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [doc for _, _, doc in scored[:top_k]]
