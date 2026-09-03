"""Web 服务：把 NL -> DSL -> SQL -> 结果 -> 解释 + 图表 串成可交互界面。"""

from web.service import ensure_db, run_query

__all__ = ["ensure_db", "run_query"]
