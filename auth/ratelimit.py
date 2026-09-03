"""登录失败指数退避限流（用户名 + IP）。"""

from __future__ import annotations

import threading
import time

from config import settings


class LoginRateLimitError(RuntimeError):
    """登录暂时被限流。"""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"登录失败次数过多，请 {retry_after} 秒后重试")


class LoginRateLimiter:
    def __init__(
        self, max_failures: int = 5, base_seconds: float = 2.0, max_seconds: float = 300.0
    ) -> None:
        self.max_failures = max_failures
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            entry = self._failures.get(key)
            if entry is None:
                return
            failures, last_failure = entry
            delay = min(
                self.base_seconds * (2 ** max(0, failures - self.max_failures)), self.max_seconds
            )
            remaining = delay - (time.monotonic() - last_failure)
            if failures >= self.max_failures and remaining > 0:
                raise LoginRateLimitError(max(1, int(remaining + 0.999)))

    def record_failure(self, key: str) -> None:
        with self._lock:
            failures, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (failures + 1, time.monotonic())

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


_default_limiter: LoginRateLimiter | None = None
_limiter_lock = threading.Lock()


def default_login_limiter() -> LoginRateLimiter:
    """进程内复用的默认限流器（参数由 settings 注入，可配）。"""
    global _default_limiter
    if _default_limiter is None:
        with _limiter_lock:
            if _default_limiter is None:
                _default_limiter = LoginRateLimiter(
                    max_failures=settings.AUTH_LOGIN_MAX_FAILURES,
                    base_seconds=settings.AUTH_LOGIN_BASE_SECONDS,
                    max_seconds=settings.AUTH_LOGIN_MAX_SECONDS,
                )
    return _default_limiter
