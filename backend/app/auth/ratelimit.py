"""Bounded single-process login rate limiter; see docs/security.md."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, max_keys: int = 10_000) -> None:
        if min(max_attempts, window_seconds, max_keys) < 1:
            raise ValueError("rate-limit bounds must be positive")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._attempts: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._next_cleanup = 0.0

    def _key(self, username: str, client: str) -> str:
        return f"{username.lower()}|{client}"

    def _trim(self, bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= self.window_seconds:
            bucket.popleft()

    def _prune(self, now: float) -> None:
        if now < self._next_cleanup:
            return
        for key, bucket in list(self._attempts.items()):
            self._trim(bucket, now)
            if not bucket:
                del self._attempts[key]
        self._next_cleanup = now + min(self.window_seconds, 60)

    def check(self, username: str, client: str) -> bool:
        """Checks do not allocate; saturation denies new keys until expiry."""
        now = time.monotonic()
        key = self._key(username, client)
        with self._lock:
            self._prune(now)
            bucket = self._attempts.get(key)
            if bucket is None:
                return len(self._attempts) < self.max_keys
            self._trim(bucket, now)
            return len(bucket) < self.max_attempts

    def record_failure(self, username: str, client: str) -> None:
        now = time.monotonic()
        key = self._key(username, client)
        with self._lock:
            self._prune(now)
            bucket = self._attempts.get(key)
            if bucket is None:
                if len(self._attempts) >= self.max_keys:
                    return  # check() denies new keys while the bounded store is full.
                bucket = deque(maxlen=self.max_attempts)
                self._attempts[key] = bucket
            self._trim(bucket, now)
            bucket.append(now)

    def reset(self, username: str, client: str) -> None:
        with self._lock:
            self._attempts.pop(self._key(username, client), None)
