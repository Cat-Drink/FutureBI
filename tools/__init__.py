"""tools 包：受控工具协议 + 注册中心 + 内置工具。

- base.py      统一工具协议（BaseTool / ToolResult / ToolContext / DisplayType）
- registry.py  工具注册中心（单例、装饰器、Function Calling 规范适配）
- builtins/    标准内置工具（查询 / 趋势 / 导出 / 口径解释）

导入本包即注册全部内置工具（见 builtins），Agent 调度内核通过
``default_registry()`` 获取受控能力白名单。
"""

from __future__ import annotations

from tools import builtins as _builtins  # noqa: F401  触发内置工具自注册
from tools.base import BaseTool, DisplayType, ToolContext, ToolResult
from tools.registry import (
    DuplicateToolError,
    ToolRegistry,
    UnknownToolError,
    default_registry,
    register_tool,
)

__all__ = [
    "BaseTool",
    "DisplayType",
    "DuplicateToolError",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "UnknownToolError",
    "default_registry",
    "register_tool",
]
