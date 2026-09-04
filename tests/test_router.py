"""意图路由 + 语义澄清反问（P0）单元测试。

覆盖：
- 显式三分类：Text2SQL / 口径文档 RAG 检索 / 闲聊拒绝；
- 语义澄清：缺失时间窗口、未定义业务指标（高活用户）主动反问；
- 禁止静默回退默认值：启发式不把未定义指标近似映射为已有指标；
- RAG 检索返回口径文档；
- web.service.run_query 的路由响应（intent / action / clarifications / documents）。
"""

from __future__ import annotations

import pytest

from agent.clarify import detect_clarifications, undefined_metric_terms
from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from agent.intent import Intent, classify_intent
from agent.rag import retrieve
from agent.router import Action, route_query
from web.service import run_query


# --------------------------------------------------------------------------- #
# 显式三分类
# --------------------------------------------------------------------------- #
def test_classify_intent_text2sql():
    assert classify_intent("各品类成功订单的GMV分布？") == Intent.TEXT2SQL
    assert classify_intent("2024年6月成功订单的GMV是多少？") == Intent.TEXT2SQL


def test_classify_intent_rag():
    assert classify_intent("GMV的口径是什么？") == Intent.RAG
    assert classify_intent("退款率怎么算？") == Intent.RAG
    assert classify_intent("ARPU的定义是什么？") == Intent.RAG


def test_classify_intent_chitchat():
    assert classify_intent("今天天气怎么样？") == Intent.CHITCHAT
    assert classify_intent("你好") == Intent.CHITCHAT


# --------------------------------------------------------------------------- #
# 路由结果：动作分派
# --------------------------------------------------------------------------- #
def test_route_text2sql_passes_through():
    r = route_query("2024年6月成功订单的GMV是多少？")
    assert r.intent == Intent.TEXT2SQL
    assert r.action == Action.TEXT2SQL


def test_route_chitchat_rejected():
    r = route_query("今天天气怎么样？")
    assert r.intent == Intent.CHITCHAT
    assert r.action == Action.CHITCHAT
    assert r.message


def test_route_rag_returns_documents():
    r = route_query("GMV的口径是什么？")
    assert r.intent == Intent.RAG
    assert r.action == Action.RAG
    assert r.documents
    assert r.documents[0].key == "gmv"


def test_route_clarify_missing_time_window():
    r = route_query("成功订单的GMV是多少？")
    assert r.action == Action.CLARIFY
    assert len(r.clarifications) == 1
    assert r.clarifications[0].kind == "missing_time_window"


def test_route_clarify_undefined_metric():
    r = route_query("高活用户的GMV是多少？")
    assert r.action == Action.CLARIFY
    assert r.clarifications[0].kind == "undefined_metric"
    assert r.clarifications[0].term == "高活用户"


def test_route_clarify_high_active_users():
    r = route_query("高活跃用户的订单数？")
    assert r.action == Action.CLARIFY
    assert r.clarifications[0].kind == "undefined_metric"
    assert r.clarifications[0].term == "高活跃用户"


# --------------------------------------------------------------------------- #
# 语义澄清：未定义业务指标
# --------------------------------------------------------------------------- #
def test_undefined_metric_terms_detected():
    assert "高活用户" in undefined_metric_terms("高活用户的GMV是多少？")
    assert "高活跃用户" in undefined_metric_terms("高活跃用户的订单数？")


def test_defined_metrics_not_flagged_as_undefined():
    for q in ("活跃用户数是多少？", "去重用户数是多少？", "各品类GMV分布？"):
        assert undefined_metric_terms(q) == [], q


def test_detect_clarifications_empty_for_clear_query():
    assert detect_clarifications("2024年6月各品类成功订单的GMV分布？") == []


# --------------------------------------------------------------------------- #
# 禁止静默回退默认值：启发式拒绝未定义指标
# --------------------------------------------------------------------------- #
def test_heuristic_rejects_high_active_users():
    """未定义指标（高活跃用户）必须拒绝，绝不能静默映射为 active_users。"""
    h = DeterministicNL2DSL()
    with pytest.raises(PipelineError):
        h.run("高活跃用户的订单数？")
    with pytest.raises(PipelineError):
        h.run("高活用户的GMV是多少？")


def test_heuristic_still_parses_defined_metrics():
    h = DeterministicNL2DSL()
    dsl = h.run("活跃用户数是多少？")
    assert dsl.metrics[0].alias == "active_users"


# --------------------------------------------------------------------------- #
# 口径文档 RAG 检索
# --------------------------------------------------------------------------- #
def test_retrieve_gmv_doc():
    docs = retrieve("GMV的口径是什么？")
    assert docs and docs[0].key == "gmv"


def test_retrieve_refund_rate_doc():
    docs = retrieve("退款率怎么算？")
    assert docs and docs[0].key == "refund_rate"


def test_retrieve_empty_for_unrelated():
    assert retrieve("今天天气怎么样？") == []


def test_retrieve_semantic_without_exact_alias():
    """P0-4：无精确别名、仅有语义相近表述时，TF-IDF 稀疏向量余弦召回正确文档。"""
    docs = retrieve("平均每单的金额怎么算")
    assert docs and docs[0].key == "avg_order_amount"
    docs2 = retrieve("每个用户的消费水平")
    assert docs2 and docs2[0].key == "arpu"


# --------------------------------------------------------------------------- #
# web.service.run_query 路由响应
# --------------------------------------------------------------------------- #
def test_run_query_text2sql_has_intent(conn):
    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" not in result
    assert result["intent"] == "text2sql"
    assert result["action"] == "text2sql"
    assert result["columns"] == ["gmv"]


def test_run_query_chitchat_rejects(conn):
    result = run_query("今天天气怎么样", conn=conn)
    assert result["intent"] == "chitchat"
    assert result["action"] == "chitchat"
    assert "error" in result


def test_run_query_clarify_returns_questions(conn):
    result = run_query("高活用户的GMV是多少？", conn=conn)
    assert result["intent"] == "text2sql"
    assert result["action"] == "clarify"
    assert result["clarifications"][0]["kind"] == "undefined_metric"
    assert result["clarifications"][0]["term"] == "高活用户"
    assert "error" in result


def test_run_query_rag_returns_documents(conn):
    result = run_query("GMV的口径是什么？", conn=conn)
    assert result["intent"] == "rag"
    assert result["action"] == "rag"
    assert result["documents"]
    assert result["documents"][0]["key"] == "gmv"
