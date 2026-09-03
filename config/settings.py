"""全局配置：项目根目录、本地 DuckDB 路径与评测锚点日期。

说明：
- AS_OF_DATE 是 mock 数据生成与评测的统一时间锚点（数据与 golden 期望 SQL 均基于它），
  保证评测在任意机器、任意日期上都是确定性、可复现的。
- 生产环境由 Agent 在 TimeFilter.reference_date 中注入"今天"，此处仅作为缺省回退。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

# 本地开发零成本数仓文件（模块 C 生成）
DB_PATH: Path = PROJECT_ROOT / "analytics_sandbox.duckdb"

# 数据与评测统一锚点日期
AS_OF_DATE: date = date(2024, 6, 30)
