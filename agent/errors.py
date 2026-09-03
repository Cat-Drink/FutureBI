"""Agent 层异常定义。"""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Agent 无法把自然语言可靠地转成合法 DSL 时抛出。

    语义：宁可不答，也不猜测。调用方不应将其当作业务失败，而应视为
    "超出当前受控范围" 的拒绝信号。
    """
