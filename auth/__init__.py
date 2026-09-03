"""统一身份认证（P0）：身份库 / JWT / 会话 / 鉴权网关。

核心不变式：principal 永远由服务端从已认证身份映射（auth.gateway /
auth.identity），客户端无法指定自己的 principal。
"""

from __future__ import annotations

from auth.errors import (
    AuthenticationError,
    AuthError,
    AuthorizationError,
    TokenError,
)
from auth.gateway import AuthContext, authenticate, create_session, default_identity_store
from auth.identity import IdentityStore, User, hash_password, verify_password
from auth.session import Session, SessionStore, default_session_store
from auth.tokens import create_token, decode_token

__all__ = [
    "AuthContext",
    "AuthError",
    "AuthenticationError",
    "AuthorizationError",
    "IdentityStore",
    "Session",
    "SessionStore",
    "TokenError",
    "User",
    "authenticate",
    "create_session",
    "create_token",
    "decode_token",
    "default_identity_store",
    "default_session_store",
    "hash_password",
    "verify_password",
]
