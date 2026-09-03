"""Agent 编排层公共入口：run_pipeline(query) -> QueryDSL。

自动分派：
- 配置了 LLM_API_KEY  -> 使用 LLMNL2DSL（LLM 产出 JSON + 严格校验 + 重试）；
- 未配置（离线）      -> 使用 DeterministicNL2DSL 启发式兜底。

两者都不允许返回裸 SQL；失败统一抛 PipelineError（拒绝而非猜测）。
"""
from __future__ import annotations

from functools import lru_cache

from agent.agent import LLMNL2DSL
from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from agent.llm import OpenAICompatClient
from config import settings
from semantic.dsl_schema import QueryDSL

__all__ = ["run_pipeline", "PipelineError"]


@lru_cache(maxsize=1)
def _default_agent() -> object:
    """构造默认 Agent（按配置分派，进程内缓存）。"""
    if settings.LLM_API_KEY:
        client = OpenAICompatClient(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT,
        )
        return LLMNL2DSL(client, max_retries=settings.LLM_MAX_RETRIES)
    return DeterministicNL2DSL()


def run_pipeline(query: str) -> QueryDSL:
    """自然语言 -> QueryDSL 插槽（生产入口）。"""
    return _default_agent().run(query)

