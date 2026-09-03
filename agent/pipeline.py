"""Agent 编排层插槽：run_pipeline(query) -> QueryDSL。

当前脚手架阶段尚未接入 LLM，因此这里只定义稳定的接口契约：
- 输入：自然语言提问字符串；
- 输出：符合 semantic.dsl_schema.QueryDSL 的 Pydantic 对象；
- 失败：抛出 PipelineError（不会返回裸 SQL）。

评测器（eval.eval_runner）在未注入真实 Agent 前，使用 golden oracle 自闭环，
从而验证 "DSL -> SQL -> 执行结果" 的后半段链路是确定可靠的。
"""
from __future__ import annotations

from semantic.dsl_schema import QueryDSL


class PipelineError(RuntimeError):
    """Agent 无法把自然语言转成合法 DSL 时抛出。"""


def run_pipeline(query: str) -> QueryDSL:
    """自然语言 -> QueryDSL 插槽。

    二期接入 LLM Agent 后在此实现：由 LLM 产出 JSON，再经 QueryDSL.model_validate
    严格校验，校验不过即拒绝（零幻觉：宁可不答，不猜 SQL）。
    """
    raise NotImplementedError(
        "run_pipeline 尚未接入 LLM Agent；评测阶段请使用 eval.eval_runner 的 "
        "golden oracle（默认已注入）"
    )
