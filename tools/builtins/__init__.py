"""标准内置工具集合（导入即自注册到默认注册中心）。

内置工具清单（名称 / 职责）：
- query_metric       查即时指标（点查/汇总/分布/排行），复用受控查询链路；
- trend_analysis     趋势与周期对比（同环比/粒度/补零），复用受控查询链路；
- export_report      导出报表/数据下钻（CSV/Markdown/JSON + 截断/脱敏 + 下载链接）；
- explain_glossary   口径与指标解释（术语字典检索，不触发数据库）。

``import tools.builtins`` 后即可通过 ``default_registry().get_tool(name)``
调度这些工具；注册逻辑见 tools/registry.py。
"""

from __future__ import annotations

# 先 import 触发工具类定义，再批量注册（确定性、无副作用外的隐藏逻辑）
from tools.builtins.explain_glossary_tool import ExplainGlossaryTool, explain_glossary_tool
from tools.builtins.export_report_tool import ExportReportTool, export_report_tool
from tools.builtins.query_metric_tool import QueryMetricTool, query_metric_tool
from tools.builtins.trend_analysis_tool import TrendAnalysisTool, trend_analysis_tool
from tools.registry import default_registry

_BUILTIN_INSTANCES = (
    query_metric_tool,
    trend_analysis_tool,
    export_report_tool,
    explain_glossary_tool,
)

_registered = False


def register_builtins(registry=None) -> None:
    """把全部内置工具注册进目标（或默认）注册中心（幂等）。"""
    target = registry or default_registry()
    for tool in _BUILTIN_INSTANCES:
        try:
            target.register(tool)
        except Exception:
            # 同进程重复导入时可能已注册；幂等吞掉重复注册异常
            if not target.has(tool.name):
                raise


# 模块导入时自注册一次（进程内单例，幂等）
if not _registered:
    register_builtins()
    _registered = True


__all__ = [
    "ExplainGlossaryTool",
    "ExportReportTool",
    "QueryMetricTool",
    "TrendAnalysisTool",
    "explain_glossary_tool",
    "export_report_tool",
    "query_metric_tool",
    "register_builtins",
    "trend_analysis_tool",
]
