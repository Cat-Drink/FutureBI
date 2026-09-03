"""LLM 驱动的 NL -> DSL Agent。

流程（零幻觉约束）：
1. 调用 LLM，要求只输出 QueryDSL JSON；
2. 从输出中提取 JSON（容忍少量 Markdown 代码块包裹）；
3. QueryDSL.model_validate 严格校验 —— 任何未知字段/非法枚举直接判失败；
4. 校验失败时把错误反馈给 LLM 重试（最多 max_retries 次）；
5. 重试耗尽仍失败 -> 抛 PipelineError（拒绝，绝不猜测）。

该实现不直接接触 SQL：DSL 产出后交给 compiler 编译，从机制上杜绝注入。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from agent.errors import PipelineError
from agent.llm import OpenAICompatClient
from agent.prompts import build_fix_messages, build_messages
from semantic.dsl_schema import QueryDSL

BT = chr(96)  # backtick
FENCE = BT * 3


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 文本中提取 JSON 对象。

    优先直接解析；失败则尝试剥离 Markdown 代码块围栏后再解析。
    """
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        raise ValueError("顶层不是 JSON 对象")
    except json.JSONDecodeError:
        pass

    fence = re.search(FENCE + r"(?:json)?\s*(.*?)\s*" + FENCE, text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("输出中未找到 JSON 对象")


class LLMNL2DSL:
    """基于 LLM 的 NL -> DSL Agent。"""

    def __init__(self, client: OpenAICompatClient, max_retries: int = 2) -> None:
        self.client = client
        self.max_retries = max_retries

    def run(self, query: str) -> QueryDSL:
        last_error: Exception | None = None
        messages = build_messages(query)
        for _ in range(self.max_retries + 1):
            raw = self.client.chat(messages)
            try:
                obj = extract_json(raw)
                if "error" in obj and obj.get("error"):
                    raise PipelineError("LLM 拒绝解析: " + str(obj["error"]))
                return QueryDSL.model_validate(obj)
            except (ValueError, TypeError, KeyError, ValidationError, PipelineError) as exc:
                last_error = exc
                messages = build_fix_messages(query, raw, str(exc)[:400])
        raise PipelineError(
            "LLM 重试 " + str(self.max_retries) + " 次后仍无法产出合法 DSL: " + str(last_error)
        ) from last_error
