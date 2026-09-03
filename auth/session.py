"""服务端会话存储（进程内、线程安全）。

会话是不透明的服务端签发凭证：客户端只持有 session_id，服务端据此从
SessionStore 解析出 username / principal。与 JWT 互补：支持"登出即失效"，
适合浏览器 Cookie 场景。到期自动失效并惰性清理。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

from config import settings


@dataclass(frozen=True)
class Session:
    session_id: str
    username: str
    principal: str
    created_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class SessionStore:
    """线程安全的会话注册表（默认进程内存储，TTL 可配）。"""

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.AUTH_SESSION_TTL
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str, principal: str, ttl_seconds: int | None = None) -> Session:
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        session = Session(
            session_id=uuid.uuid4().hex,
            username=username,
            principal=principal,
            created_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """按 id 取会话；已过期 / 不存在返回 None。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expired:
                self._sessions.pop(session_id, None)
                return None
            return session

    def revoke(self, session_id: str) -> bool:
        """主动登出：删除会话，返回是否存在。"""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def prune(self) -> int:
        """清理全部过期会话，返回清理条数。"""
        now = time.time()
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if now >= s.expires_at]
            for sid in expired:
                self._sessions.pop(sid, None)
            return len(expired)


_default_store: SessionStore | None = None
_store_lock = threading.Lock()


def default_session_store() -> SessionStore:
    """进程内复用的默认会话存储（按 settings.AUTH_SESSION_TTL 配置 TTL）。"""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = SessionStore()
    return _default_store
