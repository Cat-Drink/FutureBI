"""口径文档 RAG 检索（零依赖、确定性、可复现）。

对"指标口径 / 定义 / 怎么算"类问题，从 agent.glossary.GLOSSARY 中检索相关口径文档。

检索实现（P0-4 升级：字符 bigram 重叠 -> TF-IDF 稀疏向量余弦）：
- 特征：字符 bigram（中文相邻二字 / 英文相邻字符），零分词依赖；
- 权重：TF-IDF——语料中稀有而区分度高的 bigram 权重更高，比朴素重叠更接近
  "语义相关"（等价于轻量 sparse vector / 词袋向量检索，无需外部向量库与模型）；
- 别名/标题命中：命中别名或标题时给予强加权（口径检索最强的确定性信号）；
- 确定性：无随机、无网络、无外部模型，任意机器结果一致，可单测可评测。

principal 非 None 时按主体过滤口径文档（守卫前移）：越权指标的口径文档
不会出现在检索结果中。
"""

from __future__ import annotations

import math
import re

from agent.glossary import GlossaryDoc, scoped_glossary

# 余弦相似度的最低有效值（低于该值视为无关，避免噪声 bigram 造成误召回）
_MIN_COSINE = 0.02


def _bigrams(text: str) -> set[str]:
    """把文本切成字符 bigram（中文按相邻二字，英文小写后按相邻字符）。"""
    text = re.sub(r"\s+", "", text.lower())
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _tfidf_index(corpus: tuple[GlossaryDoc, ...]) -> dict[str, float]:
    """构建语料级 IDF 权重表：log((1+N)/(1+df)) + 1（平滑，罕见词权重更高）。"""
    df: dict[str, int] = {}
    for doc in corpus:
        for gram in _bigrams(doc.title + " " + doc.definition):
            df[gram] = df.get(gram, 0) + 1
    n = max(len(corpus), 1)
    return {gram: math.log((1 + n) / (1 + cnt)) + 1.0 for gram, cnt in df.items()}


def _vector(grams: set[str], idf: dict[str, float]) -> dict[str, float]:
    """bigram 集合 -> TF-IDF 稀疏向量（TF 恒为 1，乘以 IDF）。"""
    return {gram: idf.get(gram, 1.0) for gram in grams}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """两个稀疏向量的余弦相似度。"""
    if not a or not b:
        return 0.0
    common = a.keys() & b.keys()
    dot = sum(a[gram] * b[gram] for gram in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def retrieve(query: str, top_k: int = 3, principal: str | None = None) -> list[GlossaryDoc]:
    """检索与 query 最相关的口径文档，按相关度降序返回至多 top_k 条。

    评分 = 别名/标题命中强加权 + TF-IDF 稀疏向量余弦（P0-4）。
    """
    q = query.lower()
    q_grams = _bigrams(query)
    corpus = scoped_glossary(principal)
    idf = _tfidf_index(corpus)
    q_vec = _vector(q_grams, idf)

    scored: list[tuple[float, int, GlossaryDoc]] = []
    for idx, doc in enumerate(corpus):
        score = 0.0
        for alias in doc.aliases:
            if alias.lower() in q:
                score += len(alias) * 4.0
        if doc.title.lower() in q:
            score += 8.0
        doc_vec = _vector(_bigrams(doc.title + " " + doc.definition), idf)
        sim = _cosine(q_vec, doc_vec)
        if sim >= _MIN_COSINE:
            score += sim * 10.0
        if score > 0:
            scored.append((score, -idx, doc))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [doc for _, _, doc in scored[:top_k]]
