"""Agent 编排层公共入口：run_pipeline(query) -> QueryDSL。

自动分派：
- 配置了 LLM_API_KEY  -> 使用 LLMNL2DSL（LLM 产出 JSON + 严格校验 + 重试）；
- 未配置（离线）      -> 使用 DeterministicNL2DSL 启发式兜底。

两者都不允许返回裸 SQL；失败统一抛 PipelineError（拒绝而非猜测）。

可选 principal 参数：在 DSL 生成后施加安全守卫（表级/列级/行级 RLS），
见 security.guard.apply_policy。
"""

from __future__ import annotations

from functools import lru_cache

from agent.agent import LLMNL2DSL
from agent.errors import PipelineError
from agent.heuristic import DeterministicNL2DSL
from agent.llm import OpenAICompatClient
from config import settings
from security.guard import apply_policy
from semantic.dsl_schema import QueryDSL

__all__ = ["PipelineError", "run_pipeline"]


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


def run_pipeline(query: str, principal: str | None = None) -> QueryDSL:
    """自然语言 -> QueryDSL 插槽（生产入口）。

    principal 非 None 时，对生成的 DSL 施加安全守卫（表/列/行级权限）。
    """
    dsl = _default_agent().run(query)
    return apply_policy(dsl, principal)


def rewrite_dsl(
    query: str,
    dsl: QueryDSL,
    error: str,
    attempts: int = 1,
) -> QueryDSL:
    """SQL 执行自愈：把精确的编译/引擎报错喂回 LLM，重写 DSL。

    仅当配置了 LLM（LLMNL2DSL）时才有意义；确定性兜底会抛 PipelineError，
    由调用方透传原始执行报错。attempts 为修正轮数（至少 1 次）。
    """
    agent = _default_agent()
    rewrite = getattr(agent, "rewrite", None)
    if rewrite is None:
        raise PipelineError("当前 Agent 不支持 SQL 自愈重写")
    return rewrite(query, dsl, error, attempts=attempts)
