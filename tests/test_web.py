"""Web 服务层单元测试：run_query 完整链路 + HTTP 冒烟。"""

from __future__ import annotations

import json
import threading
import urllib.request

from web.server import Handler, ThreadingHTTPServer
from web.service import run_query


def test_run_query_number(conn):
    """单值查询：GMV -> number 图表。"""
    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" not in result
    assert result["columns"] == ["gmv"]
    assert len(result["rows"]) == 1
    assert result["viz"]["chart"] == "number"
    assert "求和订单金额" in result["explanation"]
    assert "SELECT" in result["sql"]


def test_run_query_dimension_pie(conn):
    """单维度分组 -> pie/bar 图表 + 多行结果。"""
    result = run_query("各品类成功订单的GMV分布？", conn=conn)
    assert "error" not in result
    assert result["columns"] == ["category", "gmv"]
    assert result["viz"]["chart"] in ("pie", "bar")
    assert result["viz"]["x"] == "category"
    assert result["viz"]["y"] == "gmv"
    assert len(result["rows"]) > 0


def test_run_query_trend_line(conn):
    """时间趋势 -> line 图表。"""
    result = run_query("2024年6月每日成功订单GMV趋势？", conn=conn)
    assert "error" not in result
    assert result["viz"]["chart"] == "line"
    assert len(result["rows"]) > 0


def test_run_query_security_denied(conn):
    """restricted 主体查询退款 -> 返回 error。"""
    result = run_query("各品类成功订单的退款金额是多少？", principal="restricted", conn=conn)
    assert "error" in result
    assert "无权" in result["error"]


def test_run_query_unknown_question(conn):
    """无法解析的问题 -> 返回 error 而非抛异常。"""
    result = run_query("今天天气怎么样", conn=conn)
    assert "error" in result


def test_http_smoke():
    """启动临时 HTTP 服务，验证 /api/health 与静态页可达。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
            assert json.loads(resp.read().decode("utf-8")) == {"status": "ok"}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        assert "FutureBI" in html
    finally:
        server.shutdown()
        server.server_close()


def test_run_query_self_heal_rewrites(conn, monkeypatch):
    """执行报错 -> 喂回 LLM 重写（至少 1 次）-> 重试成功，rewrites 计数。"""
    import web.service as svc
    from exec.guards import SqlExecutionError

    calls = {"n": 0}
    real_execute = svc.execute_sql

    def fake_execute(c, sql, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SqlExecutionError("Binder Error: 模拟精确引擎报错")
        return real_execute(c, sql, **kwargs)

    monkeypatch.setattr(svc, "execute_sql", fake_execute)
    # 模拟 LLM 重写：原样返回 DSL（验证重试链路而非重写质量）
    monkeypatch.setattr(svc, "rewrite_dsl", lambda q, d, e, attempts=1, principal=None: d)

    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" not in result
    assert result["rewrites"] == 1
    assert calls["n"] == 2
    assert result["columns"] == ["gmv"]


def test_run_query_scan_cap_self_heals(conn, monkeypatch):
    """扫描行数熔断 -> 喂回 LLM 重写后放行。"""
    import web.service as svc
    from exec.guards import MaxRowsScannedExceeded

    calls = {"n": 0}
    real_execute = svc.execute_sql

    def fake_execute(c, sql, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise MaxRowsScannedExceeded("扫描行数上限熔断：本次查询扫描 99999999 行，超过上限 10")
        return real_execute(c, sql, **kwargs)

    monkeypatch.setattr(svc, "execute_sql", fake_execute)
    monkeypatch.setattr(svc, "rewrite_dsl", lambda q, d, e, attempts=1, principal=None: d)

    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" not in result
    assert result["rewrites"] == 1


def test_run_query_exec_error_surfaces_without_llm(conn, monkeypatch):
    """确定性兜底（无 LLM）下执行失败：返回友好话术，技术细节在 error_detail。"""
    import web.service as svc
    from exec.guards import SqlExecutionError

    def fake_execute(c, sql, **kwargs):
        raise SqlExecutionError("Binder Error: 模拟精确引擎报错")

    monkeypatch.setattr(svc, "execute_sql", fake_execute)

    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" in result
    assert "查询执行出错" in result["error"]
    assert "error_detail" in result
    assert "Binder Error" in result["error_detail"]


def test_run_query_degrades_to_heuristic_when_llm_fails(conn, monkeypatch):
    """P0-5：已配 Key 但 LLM 网络故障 -> 降级到确定性启发式并标记 degraded。"""
    import agent.pipeline as pipeline
    from agent.llm import LLMError

    def boom(*args, **kwargs):
        raise LLMError("LLM 网络/服务错误: Connection refused")

    monkeypatch.setattr(pipeline, "_default_agent", boom)

    result = run_query("2024年6月成功订单的GMV是多少？", conn=conn)
    assert "error" not in result
    assert result["degraded"] is True
    assert result["mode"] == "degraded"
    assert result["columns"] == ["gmv"]
