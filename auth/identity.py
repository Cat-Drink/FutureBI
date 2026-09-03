"""身份库：用户 -> 角色 -> 主体(principal) 的服务端映射（P0）。

安全约束：principal 只能由服务端从"已认证身份"映射而来（IdentityStore），
客户端永远无法指定自己的 principal。口令以 PBKDF2-SHA256 哈希存储，比对使用
恒定时间比较（hmac.compare_digest），杜绝时序侧信道。

用户注册表来源（优先级）：
1. settings.AUTH_USERS_FILE 指向的 JSON 文件（存在则加载，便于运维改动）；
2. 否则使用内置 DEFAULT_USERS（与仓库内 auth/users.json 内容一致）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auth.errors import AuthenticationError
from config import settings

# 口令哈希参数（与 auth/users.json 内哈希保持一致）
_PBKDF2_ITERATIONS = 200_000
_SALT = b"futurebi-salt-v1"


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 口令哈希（供初始化用户注册表使用）。"""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _SALT, _PBKDF2_ITERATIONS).hex()


def verify_password(password: str, password_hash: str) -> bool:
    """恒定时间口令比对；空哈希一律拒绝。"""
    if not password_hash:
        return False
    computed = hash_password(password)
    return hmac.compare_digest(computed.encode("ascii"), password_hash.encode("ascii"))


@dataclass(frozen=True)
class User:
    """一个已登记用户（身份库条目）。"""

    username: str
    display_name: str
    # 服务端映射的策略主体（security.policy.POLICIES 的键）——只读派生，客户端不可改
    principal: str
    roles: frozenset[str] = frozenset()
    password_hash: str = ""
    enabled: bool = True


# 内置用户注册表（与 auth/users.json 一致）：
#   admin   -> 主体 admin（全表无限制）
#   analyst -> 主体 analyst（全表但仅 5 省 RLS）
#   bob     -> 主体 restricted（无退款表、无敏感列、仅广东）
DEFAULT_USERS: dict[str, dict[str, Any]] = {
    "admin": {
        "display_name": "系统管理员",
        "principal": "admin",
        "roles": ["admin"],
        "password_hash": "c51d8b1eb8b673c9c82da9525ffe83b073bc33fc99ba1018b22a98bde1ad4774",
    },
    "analyst": {
        "display_name": "分析师",
        "principal": "analyst",
        "roles": ["analyst"],
        "password_hash": "62ed6d0623d447181fb0d119dedb72256e3683a0516e441168cb08b33832c915",
    },
    "bob": {
        "display_name": "受限运营",
        "principal": "restricted",
        "roles": ["ops"],
        "password_hash": "088cf380cabf52cb62501ede585755a298b09882d8e0a84315470e395f336bff",
    },
}


class IdentityStore:
    """服务端身份库：认证与 principal 映射的唯一事实来源。"""

    def __init__(self, users_file: Path | str | None = None) -> None:
        self._users: dict[str, User] = {}
        self._load(Path(users_file) if users_file else settings.AUTH_USERS_FILE)

    def _load(self, path: Path | None) -> None:
        raw: dict[str, Any]
        if path is not None and path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("users", data)
        else:
            raw = DEFAULT_USERS
        for username, info in raw.items():
            self._users[str(username)] = User(
                username=str(username),
                display_name=str(info.get("display_name", username)),
                principal=str(info["principal"]),
                roles=frozenset(str(r) for r in info.get("roles", [])),
                password_hash=str(info.get("password_hash", "")),
                enabled=bool(info.get("enabled", True)),
            )

    def get(self, username: str) -> User | None:
        return self._users.get(username)

    def require_user(self, username: str) -> User:
        user = self._users.get(username)
        if user is None:
            raise AuthenticationError(f"用户不存在: {username!r}")
        if not user.enabled:
            raise AuthenticationError(f"用户已停用: {username!r}")
        return user

    def authenticate(self, username: str, password: str) -> User:
        """用户名 + 口令认证；失败抛 AuthenticationError（拒绝而非放行）。"""
        user = self.require_user(username)
        if not verify_password(password, user.password_hash):
            raise AuthenticationError("用户名或口令错误")
        return user

    def principal_for(self, username: str) -> str:
        """服务端把身份映射为策略主体（principal）。"""
        return self.require_user(username).principal
