"""登录限流（P0-4）单元测试：指数退避 + 成功重置。"""

from __future__ import annotations

import pytest

from auth.ratelimit import LoginRateLimiter, LoginRateLimitError


def test_allows_within_threshold():
    limiter = LoginRateLimiter(max_failures=3, base_seconds=1.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_failure("a:1.2.3.4")
    limiter.check("a:1.2.3.4")  # 未达阈值不抛


def test_blocks_after_threshold():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=10.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_failure("a:1.2.3.4")
    with pytest.raises(LoginRateLimitError) as excinfo:
        limiter.check("a:1.2.3.4")
    assert excinfo.value.retry_after > 0


def test_success_resets_failure_count():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=10.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_success("a:1.2.3.4")
    limiter.check("a:1.2.3.4")  # 重置后放行


def test_keys_are_isolated():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=10.0)
    limiter.record_failure("a:1.2.3.4")
    limiter.record_failure("a:1.2.3.4")
    limiter.check("b:5.6.7.8")  # 其他用户名+IP 不受影响
