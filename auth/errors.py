"""鉴权异常体系。

- AuthenticationError：身份认证失败（HTTP 401）——未提供凭证 / 凭证无效 / 过期；
- AuthorizationError：身份有效但无权执行（HTTP 403，预留）；
- TokenError：令牌签发 / 校验失败（签名错误、过期、篡改等）。
"""

from __future__ import annotations


class AuthError(RuntimeError):
    """鉴权 / 授权通用错误基类。"""


class AuthenticationError(AuthError):
    """身份认证失败：未提供凭证、凭证无效或已过期（HTTP 401）。"""


class AuthorizationError(AuthError):
    """授权失败：身份有效但无权访问（HTTP 403）。"""


class TokenError(AuthError):
    """令牌签发 / 校验失败（签名错误、过期、篡改、格式非法等）。"""
