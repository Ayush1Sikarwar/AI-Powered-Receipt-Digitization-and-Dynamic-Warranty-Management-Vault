"""Rate limiting for authentication, upload, and API endpoints (SRS Security).

Backend: Redis fixed-window counter when available, otherwise an
in-process counter (single-process development deployments). The limiter is
deliberately infrastructure-agnostic so it can be swapped for a gateway
limit in production without touching route code.
"""

import threading
import time
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class _MemoryStore:
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = time.time()
        with self._lock:
            bucket_start, count = self._buckets.get(key, (0, 0))
            if bucket_start <= now - window:
                bucket_start, count = now, 0
            count += 1
            self._buckets[key] = (bucket_start, count)
        if count > limit:
            return RateLimitResult(False, 0, max(1, int(bucket_start + window - now)))
        return RateLimitResult(True, limit - count, 0)


class RateLimiter:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._memory = _MemoryStore()
        self._redis = None
        if self._settings.use_redis:
            try:
                import redis

                self._redis = redis.Redis.from_url(
                    self._settings.redis_url, socket_connect_timeout=2, decode_responses=True
                )
                self._redis.ping()
            except Exception:
                self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def reset(self) -> None:
        """Clear all limit state (used by the test suite for determinism)."""
        self._memory.clear()
        if self._redis is not None:
            try:
                keys = [k for k in self._redis.scan_iter("rl:*", count=100)]
                if keys:
                    self._redis.delete(*keys)
            except Exception:  # noqa: BLE001
                pass

    def hit(self, key: str, limit: int, window: int | None = None) -> RateLimitResult:
        window = window or self._settings.rl_window_seconds
        if self._redis is not None:
            try:
                window_key = f"rl:{key}:{int(time.time() // window)}"
                count = self._redis.incr(window_key)
                if count == 1:
                    self._redis.expire(window_key, window)
                remaining = max(0, limit - int(count))
                if count > limit:
                    ttl = self._redis.ttl(window_key) or window
                    return RateLimitResult(False, 0, max(1, ttl))
                return RateLimitResult(True, remaining, 0)
            except Exception:
                # Fall back to memory on transient Redis failure (availability > strictness).
                pass
        return self._memory.hit(key, limit, window)


rate_limiter = RateLimiter()


def get_redis_client():
    """Return the shared Redis client, or None when unavailable.

    Used by readiness/metrics to report worker-heartbeat and queue state.
    """
    return rate_limiter._redis
