"""共享 pytest fixtures：内存 DuckDB 连接 + 灌数据。"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock.init_duckdb import build_tables  # noqa: E402  (需先注入项目根到 sys.path)


@pytest.fixture(scope="session")
def conn() -> duckdb.DuckDBPyConnection:
    """会话级内存 DuckDB，注入确定性 mock 数据。"""
    c = duckdb.connect(":memory:")
    build_tables(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean_session_memory():
    """每个测试后清空会话记忆存储与澄清槽位，避免跨测试状态泄漏。"""
    yield
    from agent.memory import default_session_store
    from agent.slotfill import default_slot_store

    default_session_store().clear_all()
    default_slot_store().clear_all()
