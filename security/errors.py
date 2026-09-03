"""安全层异常。"""

from __future__ import annotations


class SecurityError(RuntimeError):
    """权限校验失败：主体无权访问引用的表/列，或主体未登记。"""
