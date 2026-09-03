"""JWT（HS256）签发与校验 —— 纯标准库实现（零第三方依赖）。

P0 安全约束：
- 令牌只携带标准声明与 sub（用户名）；"主体 / 角色"等信息由服务端在每次
  请求时从 IdentityStore 重新映射，绝不信任令牌载荷中的任何主体声明；
- 校验强制检查：签名（HMAC-SHA256 恒定时间比较）、exp 过期时间、iss 签发者、
  aud 受众、nbf 生效时间；
- 令牌只能由持有 AUTH_JWT_SECRET 的服务端签发，客户端无法伪造。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from auth.errors import TokenError

_ALG = "HS256"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except (ValueError, TypeError) as exc:
        raise TokenError("令牌 Base64 解码失败") from exc


def _sign(header: str, payload: str, secret: str) -> str:
    msg = (header + "." + payload).encode("utf-8")
    return _b64url_encode(hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest())


def create_token(
    subject: str,
    secret: str,
    *,
    issuer: str,
    audience: str,
    ttl_seconds: int,
    now: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """签发一个 HS256 JWT。

    - subject：用户名（sub），服务端后续据此从身份库映射 principal；
    - extra：可选附加声明（仅标准字段，如 session_id 关联），不承载权限。
    """
    header = {"alg": _ALG, "typ": "JWT"}
    ts = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": ts,
        "nbf": ts,
        "exp": ts + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    enc_header = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    enc_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(enc_header, enc_payload, secret)
    return f"{enc_header}.{enc_payload}.{signature}"


def decode_token(
    token: str,
    secret: str,
    *,
    issuer: str,
    audience: str,
    now: float | None = None,
) -> dict[str, Any]:
    """校验并解码 JWT；任何不合法处抛 TokenError（拒绝而非放行）。

    返回的 claims 仅用于读取 sub 等身份标识；principal 映射在网关层重新计算。
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("令牌格式非法")
    enc_header, enc_payload, signature = parts

    expected = _sign(enc_header, enc_payload, secret)
    if not hmac.compare_digest(signature.encode("ascii"), expected.encode("ascii")):
        raise TokenError("令牌签名校验失败")

    try:
        claims = json.loads(_b64url_decode(enc_payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenError("令牌载荷解析失败") from exc
    if not isinstance(claims, dict):
        raise TokenError("令牌载荷非法")

    ts = now if now is not None else time.time()
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= ts:
        raise TokenError("令牌已过期")
    if claims.get("iss") != issuer:
        raise TokenError("令牌签发者不匹配")
    if claims.get("aud") != audience:
        raise TokenError("令牌受众不匹配")
    nbf = claims.get("nbf")
    if isinstance(nbf, int) and nbf > ts:
        raise TokenError("令牌尚未生效")
    return claims
