"""全局配置：项目根目录、本地 DuckDB 路径、评测锚点日期与 LLM 接入。

说明：
- AS_OF_DATE 是 mock 数据生成与评测的统一时间锚点（数据与 golden 期望 SQL 均基于它），
  保证评测在任意机器、任意日期上都是确定性、可复现的。
- 生产环境由 Agent 在 TimeFilter.reference_date 中注入"今天"，此处仅作为缺省回退。
- LLM 相关配置全部通过环境变量注入；未配置 LLM_API_KEY 时 Agent 自动回退到
  确定性启发式 NL2DSL（agent.heuristic），保证离线可运行、可评测。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

# 加载项目根目录 .env（若存在）。环境变量优先级高于 .env 文件，
# 便于 CI / 容器注入真实密钥。
load_dotenv(PROJECT_ROOT / ".env")

# 本地开发零成本数仓文件（模块 C 生成）
DB_PATH: Path = PROJECT_ROOT / "analytics_sandbox.duckdb"

# 数据与评测统一锚点日期
AS_OF_DATE: date = date(2024, 6, 30)

# --------------------------------------------------------------------------- #
# LLM（NL -> DSL）接入配置 —— 通过环境变量注入，见 .env.example
# --------------------------------------------------------------------------- #
# API Key；为空则 Agent 使用确定性启发式 NL2DSL 兜底
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
# OpenAI 兼容的 Chat Completions 端点
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

# --------------------------------------------------------------------------- #
# SQL 执行层资源治理（P0/P1）—— 见 exec/ 包
# --------------------------------------------------------------------------- #
# 语句超时（毫秒）：超过则中断取消查询（DuckDB 侧用线程看门狗 + interrupt() 实现）
QUERY_TIMEOUT_MS: int = int(os.getenv("QUERY_TIMEOUT_MS", "30000"))
# 扫描行数上限：任一基表扫描超过即熔断拒绝执行（EXPLAIN ANALYZE 预检）
MAX_SCAN_ROWS: int = int(os.getenv("MAX_SCAN_ROWS", "10000000"))
# 返回行数硬上限：结果超过即熔断（LIMIT 硬上限，独立于 DSL 约束的防御性校验）
MAX_RESULT_ROWS: int = int(os.getenv("MAX_RESULT_ROWS", "20000"))
# SQL 执行自愈最大重试次数（把精确引擎报错喂回 LLM 重写 DSL，至少 1 次）
SQL_SELF_HEAL_MAX_RETRIES: int = int(os.getenv("SQL_SELF_HEAL_MAX_RETRIES", "1"))

# --------------------------------------------------------------------------- #
# 审计与结构化日志（P0）—— 见 audit/ 包
# --------------------------------------------------------------------------- #
# 是否开启审计写入（对象存储 JSONL + DuckDB 审计表）
AUDIT_ENABLED: bool = os.getenv("AUDIT_ENABLED", "1").lower() not in ("0", "false", "no")
# 审计产物目录（JSONL 对象存储 + DuckDB 审计表）
AUDIT_DIR: Path = PROJECT_ROOT / "logs"
AUDIT_LOG_PATH: Path = AUDIT_DIR / "audit.jsonl"
AUDIT_DB_PATH: Path = AUDIT_DIR / "audit.duckdb"
# 结构化日志级别（web.server 启动时 setup_logging 使用）
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
