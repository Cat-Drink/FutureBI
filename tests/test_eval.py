"""Golden Dataset 端到端评测测试。"""
from __future__ import annotations

from eval.eval_runner import evaluate_all, load_golden


def test_golden_dataset_has_ten_cases():
    cases = load_golden()
    assert len(cases) == 10
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "用例 id 必须唯一"


def test_all_golden_cases_pass(conn):
    summary = evaluate_all(conn)
    assert summary.total == 10
    assert summary.failed == 0, [
        (r.id, r.error, r.dsl_ok, r.sql_ok, r.result_ok) for r in summary.reports
    ]
